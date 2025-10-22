import os  # => lister les .parquet dans un dossier, simple et suffisant
import pandas as pd  # => lire Parquet (pyarrow) et pousser vers Postgres via DataFrame.to_sql
from sqlalchemy import text  # => requêtes SQL paramétrées (sécurité + lisibilité)
from sqlalchemy.engine import Engine  # => type hint lisible (facilite la relecture/IDE)

# On réutilise le moteur SQLAlchemy centralisé (URL & pool) défini dans database.py
from database import engine


# Raison : créer les tables si absentes (idempotent) avant tout import.
YELLOW_TAXI_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS yellow_taxi_trips (
    vendorid BIGINT,
    tpep_pickup_datetime TIMESTAMP,
    tpep_dropoff_datetime TIMESTAMP,
    passenger_count DOUBLE PRECISION,
    trip_distance DOUBLE PRECISION,
    ratecodeid DOUBLE PRECISION,
    store_and_fwd_flag VARCHAR,
    pulocationid BIGINT,
    dolocationid BIGINT,
    payment_type BIGINT,
    fare_amount DOUBLE PRECISION,
    extra DOUBLE PRECISION,
    mta_tax DOUBLE PRECISION,
    tip_amount DOUBLE PRECISION,
    tolls_amount DOUBLE PRECISION,
    improvement_surcharge DOUBLE PRECISION,
    total_amount DOUBLE PRECISION,
    congestion_surcharge DOUBLE PRECISION,
    airport_fee DOUBLE PRECISION
);
"""

IMPORT_LOG_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS import_log (
    file_name VARCHAR PRIMARY KEY,
    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rows_imported BIGINT
);
"""


# --- Harmonisation des colonnes ---
# Raison : les Parquet peuvent avoir des noms en PascalCase/mixte ; on mappe en snake_case
# pour coller exactement au schéma Postgres ci-dessus.
RENAME_MAP = {
    "VendorID": "vendorid",
    "tpep_pickup_datetime": "tpep_pickup_datetime",
    "tpep_dropoff_datetime": "tpep_dropoff_datetime",
    "passenger_count": "passenger_count",
    "trip_distance": "trip_distance",
    "RatecodeID": "ratecodeid",
    "store_and_fwd_flag": "store_and_fwd_flag",
    "PULocationID": "pulocationid",
    "DOLocationID": "dolocationid",
    "payment_type": "payment_type",
    "fare_amount": "fare_amount",
    "extra": "extra",
    "mta_tax": "mta_tax",
    "tip_amount": "tip_amount",
    "tolls_amount": "tolls_amount",
    "improvement_surcharge": "improvement_surcharge",
    "total_amount": "total_amount",
    "congestion_surcharge": "congestion_surcharge",
    "Airport_fee": "airport_fee",
}


class PostgresImporter:
    """
    Responsable de :
    - Initialiser les tables (idempotent)
    - Importer 1 fichier Parquet (avec transaction + log anti-doublon)
    - Importer tous les Parquet d'un dossier (découverte via os.listdir)
    - Afficher des statistiques de contrôle
    """

    def __init__(self, engine: Engine):
        # On garde l'engine pour ouvrir des transactions à la demande.
        self.engine = engine
        self._initialize_database()  # crée les tables si absentes (sécurité)

    def _initialize_database(self) -> None:
        # engine.begin() => ouvre une transaction ; COMMIT auto si tout va bien (sinon ROLLBACK).
        with self.engine.begin() as conn:
            conn.execute(text(YELLOW_TAXI_SCHEMA_PG))
            conn.execute(text(IMPORT_LOG_SCHEMA_PG))

    def is_file_imported(self, filename: str) -> bool:
        """
        Vérifie l'idempotence : si ce nom de fichier existe déjà dans import_log,
        on considère que le fichier a été traité (on évite le doublon).
        """
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT 1 FROM import_log WHERE file_name = :fn LIMIT 1"),
                {"fn": filename},
            ).fetchone()
            return row is not None

    def import_parquet(self, file_path: str) -> bool:
        """
        Importer un fichier Parquet donné :
        - skip si déjà importé (via import_log)
        - sinon :
            * lire le Parquet avec pandas
            * renommer les colonnes pour matcher le schéma PG (RENAME_MAP)
            * transaction :
                - compter les lignes avant
                - to_sql (append) dans la même transaction (con=conn)
                - compter après
                - insérer dans import_log (file_name, rows_imported)
        """
        filename = file_path.split("/")[-1]
        if self.is_file_imported(filename):
            print(f"Déjà importé, on ignore : {filename}")
            return True

        try:
            # 1) lecture Parquet -> DataFrame (pandas/pyarrow)
            df = pd.read_parquet(file_path)

            # 2) harmonisation des noms de colonnes vers le schéma Postgres
            df = df.rename(columns=RENAME_MAP)

            # 3) robustesse : ne pousser que les colonnes attendues, dans l'ordre attendu
            expected = [
                "vendorid",
                "tpep_pickup_datetime",
                "tpep_dropoff_datetime",
                "passenger_count",
                "trip_distance",
                "ratecodeid",
                "store_and_fwd_flag",
                "pulocationid",
                "dolocationid",
                "payment_type",
                "fare_amount",
                "extra",
                "mta_tax",
                "tip_amount",
                "tolls_amount",
                "improvement_surcharge",
                "total_amount",
                "congestion_surcharge",
                "airport_fee",
            ]
            cols = [c for c in expected if c in df.columns]
            df = df[cols]

            # 4) transaction unique : count before -> to_sql -> count after -> import_log
            #    IMPORTANT : passer con=conn pour que to_sql participe à la même transaction.
            with self.engine.begin() as conn:
                before = conn.execute(text("SELECT COUNT(*) FROM yellow_taxi_trips")).scalar_one()

                df.to_sql(
                    "yellow_taxi_trips",
                    con=conn,            # même transaction
                    if_exists="append",  # append uniquement (jamais replace)
                    index=False,         # pas d'index pandas
                    method="multi",      # INSERT multi-values (performant)
                    chunksize=10_000,    # taille de lot raisonnable (RAM/disque)
                )

                after = conn.execute(text("SELECT COUNT(*) FROM yellow_taxi_trips")).scalar_one()
                rows_imported = int(after) - int(before)

                # log d'import (idempotence côté fichier)
                conn.execute(
                    text("""
                        INSERT INTO import_log (file_name, rows_imported)
                        VALUES (:fn, :rows)
                        ON CONFLICT (file_name) DO NOTHING
                    """),
                    {"fn": filename, "rows": rows_imported},
                )

            print(f"Import {filename} → {rows_imported} lignes")
            return True

        except Exception as e:
            # Le with ... begin() rollback automatiquement en cas d'exception.
            print(f"Échec import {filename} : {e}")
            return False

    def import_all_parquet_files(self, data_dir: str) -> int:
        """
        Importer tous les fichiers .parquet d'un dossier (non récursif).
        Choix volontaire : simple os.listdir() + filtre .parquet
        - pas d'argparse/glob/pathlib pour rester minimal et conforme à ta contrainte.
        """
        try:
            names = os.listdir(data_dir)  # récupère seulement les noms (pas les chemins)
        except FileNotFoundError:
            print(f"Dossier introuvable : {data_dir}")
            return 0

        files = [n for n in names if n.endswith(".parquet")]
        if not files:
            print(f"(i) Aucun .parquet dans {data_dir}")
            return 0

        files.sort()  # ordre déterministe
        ok = 0
        for name in files:
            full_path = os.path.join(data_dir, name)  # construit le chemin complet
            if self.import_parquet(full_path):
                ok += 1

        print(f"Terminé. Fichiers traités : {ok}")
        return ok

    def get_statistics(self) -> None:
        """
        Statistiques de contrôle :
        - Nombre total de trajets
        - Nombre de fichiers importés
        - Plage min/max des dates de pickup
        - Taille globale de la base (octets)
        """
        with self.engine.begin() as conn:
            total_trips = conn.execute(text("SELECT COUNT(*) FROM yellow_taxi_trips")).scalar_one()
            imported_files = conn.execute(text("SELECT COUNT(*) FROM import_log")).scalar_one()
            min_dt, max_dt = conn.execute(
                text("""
                    SELECT
                      MIN(tpep_pickup_datetime),
                      MAX(tpep_pickup_datetime)
                    FROM yellow_taxi_trips
                """)
            ).one()
            db_size = conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one()

        print("\n==== Statistiques ====")
        print(f"Total trajets          : {total_trips}")
        print(f"Fichiers importés      : {imported_files}")
        print(f"Plage pickup datetime  : {min_dt} → {max_dt}")
        print(f"Taille base (octets)   : {db_size}")

    def close(self) -> None:
        """
        Rien à fermer explicitement :
        - SQLAlchemy gère le pool de connexions
        - Les contextes 'with ... begin()' ouvrent/ferment proprement
        On expose tout de même pour rester symétrique avec d'autres impléms.
        """
        pass


# --- Exécution directe (sans argparse/sys pour rester minimal) ---
if __name__ == "__main__":
    importer = PostgresImporter(engine)
    try:
        # Dossier par défaut : là où tu as placé tes Parquet pour la partie Postgres
        default_data_dir = "src/data/raw"
        importer.import_all_parquet_files(default_data_dir)
        importer.get_statistics()
    finally:
        importer.close()

