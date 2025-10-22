import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# 🔹 Récupérer les variables d'environnement (définies dans docker-compose)
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "db")  # 'db' = nom du service PostgreSQL dans docker-compose
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "postgres")

# 🔹 URL de connexion PostgreSQL
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# 🔹 Création du moteur SQLAlchemy
engine = create_engine(DATABASE_URL, echo=True)  # echo=True pour afficher les requêtes SQL

# 🔹 Création de la session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 🔹 Base pour déclarer les modèles
Base = declarative_base()

# 🔹 Fonction utilitaire pour obtenir une session (FastAPI dependency)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 🔹 Fonction pour initialiser les tables
def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Base de données initialisée avec succès !")
    except OperationalError as e:
        print(f"❌ Erreur de connexion à la base : {e}")
