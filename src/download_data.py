from pathlib import Path
from datetime import datetime
import requests
import time

class NYCTaxiDataDownloader:
    """
    Télécharge les fichiers Parquet du NYC TLC pour plusieurs types :
    'yellow', 'green', 'fhv', 'fhvhv'.

    Si un type ne se télécharge pas correctement (ex: lien manquant),
    le programme passe automatiquement au suivant.
    """

    def __init__(self, year: int = 2025, data_dir: str = "data/raw"):
        """
        Initialise les constantes globales.
        - Crée le dossier de destination si nécessaire.
        """
        self.BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
        self.YEAR = year
        self.DATA_DIR = Path(data_dir)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Tous les types disponibles (utilisés pour la boucle automatique)
        self.VALID_TYPES = ["yellow"]

    def get_file_path(self, dataset_type: str, month: int) -> Path:
        """
        Construit le chemin local du fichier pour un type et un mois donnés.
        Format : <type>_tripdata_YYYY-MM.parquet
        """
        filename = f"{dataset_type}_tripdata_{self.YEAR}-{month:02d}.parquet"
        return self.DATA_DIR / filename

    def file_exists(self, dataset_type: str, month: int) -> bool:
        """Vérifie si le fichier existe déjà localement."""
        return self.get_file_path(dataset_type, month).exists()

    def download_month(self, dataset_type: str, month: int) -> bool:
        """
        Télécharge le fichier pour un type et un mois donnés.
        - Si le fichier existe déjà → on saute.
        - Sinon → on tente le téléchargement.
        - En cas d'erreur réseau, le fichier partiel est supprimé.
        """
        file_path = self.get_file_path(dataset_type, month)

        # Étape 1 : éviter les doublons
        if self.file_exists(dataset_type, month):
            print(f"Fichier déjà présent : {file_path.name}")
            return True

        # Étape 2 : construction de l’URL distante
        url = f"{self.BASE_URL}/{dataset_type}_tripdata_{self.YEAR}-{month:02d}.parquet"
        print(f"⬇Téléchargement : {url}")

        try:
            # Téléchargement en flux
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 8192

                with open(file_path, "wb") as f:
                    start = time.time()
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                    end = time.time()

            size_mb = downloaded / 1e6
            print(f"Terminé : {file_path.name} ({size_mb:.2f} MB en {end - start:.1f}s)")
            return True

        except requests.exceptions.RequestException as e:
            print(f"Erreur réseau ou fichier introuvable pour {dataset_type} {self.YEAR}-{month:02d} : {e}")
            # Supprime le fichier partiel
            if file_path.exists():
                file_path.unlink()
            return False

    def download_all_available(self) -> list:
        """
        Télécharge tous les fichiers disponibles pour chaque type (yellow).
        - Passe au type suivant si un fichier ou un type échoue.
        - De janvier au mois courant (si année en cours), sinon jusqu'à décembre.
        """
        now = datetime.now()
        last_month = now.month if self.YEAR == now.year else 12

        print(f"\nTéléchargement multi-types pour {self.YEAR} (1 → {last_month})")
        print(f"Types concernés : {', '.join(self.VALID_TYPES)}\n")

        all_downloaded = []

        # Boucle principale : pour chaque type
        for dataset_type in self.VALID_TYPES:
            print(f"\nDébut du téléchargement pour {dataset_type.upper()}\n")

            downloaded_type = []

            # Boucle mensuelle
            for month in range(1, last_month + 1):
                ok = self.download_month(dataset_type, month)
                if ok:
                    downloaded_type.append(self.get_file_path(dataset_type, month))
                else:
                    # Si le fichier est introuvable (404 ou erreur), on passe au mois suivant
                    continue
                time.sleep(0.3)  # pause de courtoisie

            print(f"\n DONE {len(downloaded_type)} fichiers téléchargés pour {dataset_type.upper()}.\n")
            all_downloaded.extend(downloaded_type)

        # Résumé global
        print(f"DONE Téléchargement terminé. {len(all_downloaded)} fichiers disponibles tous types confondus.")
        return all_downloaded


if __name__ == "__main__":
    downloader = NYCTaxiDataDownloader(year=2025, data_dir="src/data/raw")
    downloader.download_all_available()
