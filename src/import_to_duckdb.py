import sys
import duckdb

# Schémas imposés par la consigne
YELLOW_TAXI_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS yellow_taxi_trips (
    VendorID BIGINT,
    tpep_pickup_datetime TIMESTAMP,
    tpep_dropoff_datetime TIMESTAMP,
    passenger_count DOUBLE,
    trip_distance DOUBLE,
    RatecodeID DOUBLE,
    store_and_fwd_flag VARCHAR,
    PULocationID BIGINT,
    DOLocationID BIGINT,
    payment_type BIGINT,
    fare_amount DOUBLE,
    extra DOUBLE,
    mta_tax DOUBLE,
    tip_amount DOUBLE,
    tolls_amount DOUBLE,
    improvement_surcharge DOUBLE,
    total_amount DOUBLE,
    congestion_surcharge DOUBLE,
    Airport_fee DOUBLE,
    cbd_congestion_fee DOUBLE
);
"""

IMPORT_LOG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS import_log (
    file_name VARCHAR PRIMARY KEY,
    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rows_imported BIGINT
);
"""


class DuckDBImporter:
    def __init__(self, db_path: str):
        """Se connecter à DuckDB et initialiser la base"""
        self.db_path = db_path
        self.conn = duckdb.connect(self.db_path)
        self._initialize_database()

    def _initialize_database(self) -> None:
        """Créer les tables si elles n'existent pas."""
        self.conn.execute(YELLOW_TAXI_SCHEMA_SQL)
        self.conn.execute(IMPORT_LOG_SCHEMA_SQL)

    def is_file_imported(self, filename: str) -> bool:
        """Retourne True si `filename` est présent dans import_log."""
        row = self.conn.execute(
            "SELECT 1 FROM import_log WHERE file_name = ? LIMIT 1;",
            [filename],
        ).fetchone()
        return row is not None

    def import_parquet(self, file_path: str) -> bool:
        """Importer un fichier parquet donné par son chemin (str).
        - Vérifie le log, importe via INSERT ... SELECT * FROM read_parquet(?), compte avant/après.
        - Gère transaction + rollback si erreur.
        """
        filename = file_path.split("/")[-1]
        if self.is_file_imported(filename):
            print(f"Déjà importé, on ignore : {filename}")
            return True

        try:
            self.conn.execute("BEGIN;")
            before = self.conn.execute("SELECT COUNT(*) FROM yellow_taxi_trips;").fetchone()[0]

            # Import direct : DuckDB lit le parquet
            self.conn.execute(
                "INSERT INTO yellow_taxi_trips SELECT * FROM read_parquet(?);",
                [file_path],
            )

            after = self.conn.execute("SELECT COUNT(*) FROM yellow_taxi_trips;").fetchone()[0]
            rows_imported = int(after) - int(before)

            self.conn.execute(
                """
                INSERT INTO import_log (file_name, rows_imported)
                VALUES (?, ?)
                ON CONFLICT (file_name) DO NOTHING;
                """,
                [filename, rows_imported],
            )

            self.conn.execute("COMMIT;")
            print(f"Import {filename} → {rows_imported} lignes")
            return True
        except Exception as e:
            try:
                self.conn.execute("ROLLBACK;")
            except Exception:
                pass
            print(f"Echec import {filename} : {e}")
            return False

    def import_all_parquet_files(self, data_dir: str) -> int:
        """Importer tous les .parquet du répertoire (via glob DuckDB dans read_parquet).
        - Utilise read_parquet('<dir>/*.parquet', filename=true) pour découvrir les fichiers.
        - Pour chaque `filename` distinct, appelle import_parquet().
        """
        pattern = f"{data_dir.rstrip('/')}/*.parquet"

        try:
            # Découverte des fichiers via DuckDB (pas de pathlib/os):
            # filename=true ajoute une colonne implicite `filename` avec le chemin source
            rows = self.conn.execute(
                "SELECT DISTINCT filename FROM read_parquet(?, filename=true);",
                [pattern],
            ).fetchall()
        except Exception as e:
            # Aucun match ou dossier invalide → on informe et on sort proprement
            print(f"(i) Aucun .parquet trouvé pour le motif : {pattern} ({e})")
            return 0

        files = [r[0] for r in rows]
        if not files:
            print(f"(i) Aucun .parquet dans {data_dir}")
            return 0

        ok = 0
        for fp in sorted(files):
            if self.import_parquet(fp):
                ok += 1
        print(f"Terminé. Fichiers traités : {ok}")
        return ok

    def get_statistics(self) -> None:
        """Afficher total trajets, nb fichiers importés, min/max, taille DB (via PRAGMA database_size)."""
        total_trips = self.conn.execute("SELECT COUNT(*) FROM yellow_taxi_trips;").fetchone()[0]
        imported_files = self.conn.execute("SELECT COUNT(*) FROM import_log;").fetchone()[0]
        min_dt, max_dt = self.conn.execute(
            "SELECT MIN(tpep_pickup_datetime), MAX(tpep_pickup_datetime) FROM yellow_taxi_trips;"
        ).fetchone()

        try:
            size_row = self.conn.execute("PRAGMA database_size;").fetchone()
            # L'ordre/colonnes peuvent évoluer; DuckDB expose typiquement total_bytes en 3e pos.
            # Pour rester simple et stable, on affiche toute la ligne et le total estimé.
            # Sur versions récentes: (database_size, block_size, total_blocks, used_blocks, free_blocks, wal_size, total_bytes)
            total_bytes = size_row[-1] if size_row else 0
        except Exception:
            total_bytes = 0

        print("==== Statistiques ====")
        print(f"Total trajets          : {total_trips}")
        print(f"Fichiers importés      : {imported_files}")
        print(f"Plage pickup datetime  : {min_dt} → {max_dt}")
        print(f"Taille DB (octets)     : {total_bytes}")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# parseur CLI maison
def _get_arg(flag: str, default: str | None = None) -> str | None:
    """Retourne la valeur suivant un flag (ex: --dir mydir)."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            return sys.argv[i + 1]
        return ""
    return default


def main() -> None:
    # Valeurs par défaut imposées par la spec implicite
    data_dir = _get_arg("--dir", "data/raw")
    db_path = _get_arg("--db", "yellow_taxi.duckdb")
    stats_only = "--stats" in sys.argv

    importer = DuckDBImporter(db_path)
    try:
        if not stats_only:
            importer.import_all_parquet_files(data_dir)
        importer.get_statistics()
    finally:
        importer.close()


if __name__ == "__main__":
    main()

