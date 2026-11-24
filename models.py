import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Integer, Float, Boolean,
    DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./apotheka_v2.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Usuari(Base):
    __tablename__ = "usuaris"

    dni_nie = Column(String(15), primary_key=True, index=True)
    nom_complet = Column(String(120), nullable=False)
    edat = Column(Integer, nullable=False)
    password_hash = Column(String(128), nullable=False)
    empremta_token = Column(String(128), unique=True, nullable=True, index=True)
    alergies = Column(Text, default="")
    patologies = Column(Text, default="")
    medicacio_habitual = Column(Text, default="")
    data_registre = Column(DateTime, default=datetime.utcnow)


class CatalegClinic(Base):
    __tablename__ = "cataleg_clinic"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tipus = Column(String(30), nullable=False, index=True)
    nom_visible = Column(String(255), nullable=False)
    clau_clinica = Column(String(100), nullable=False)


class Medicament(Base):
    __tablename__ = "medicaments"

    cn = Column(String(10), primary_key=True, index=True)
    nregistro = Column(String(20), index=True)
    nom_oficial = Column(String(255), nullable=False)
    principi_actiu = Column(String(255), nullable=False)
    laboratori = Column(String(150))
    categoria = Column(String(80), nullable=False)
    requereix_recepta = Column(Boolean, nullable=False)
    url_foto = Column(String(500))
    url_prospecte = Column(String(500))
    preu = Column(Float, nullable=False)
    limit_unitats_venda = Column(Integer, default=2)
    contraindicacions = Column(Text, default="")
    recomanacio_creuada_cn = Column(String(10), nullable=True)


class LotInventari(Base):
    __tablename__ = "lots_inventari"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cn = Column(String(10), ForeignKey("medicaments.cn"), nullable=False)
    codi_lot = Column(String(40), nullable=False)
    data_caducitat = Column(String(10), nullable=False)
    quantitat = Column(Integer, nullable=False)
    canal_robot = Column(String(30), nullable=False)


class ReceptaQR(Base):
    __tablename__ = "receptes_qr"

    codi_qr = Column(String(50), primary_key=True, index=True)
    dni_nie = Column(String(15), ForeignKey("usuaris.dni_nie"), nullable=False)
    cn = Column(String(10), ForeignKey("medicaments.cn"), nullable=False)
    quantitat_prescrita = Column(Integer, default=1)
    dispensada = Column(Boolean, default=False)


class RegistreDispensacio(Base):
    __tablename__ = "registre_dispensacions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_hora = Column(DateTime, default=datetime.utcnow)
    dni_nie = Column(String(15), nullable=True)
    cn = Column(String(10), nullable=False)
    nom_medicament = Column(String(255), nullable=False)
    codi_lot = Column(String(40), nullable=False)
    data_caducitat = Column(String(10), nullable=False)
    quantitat = Column(Integer, nullable=False)
    tipus_venda = Column(String(50), nullable=False)


class ComandaCua(Base):
    __tablename__ = "comandes_cua"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_hora = Column(DateTime, default=datetime.utcnow)
    origen = Column(String(30), nullable=False)  # "Taulell / 24-7" | "Recepta QR Exterior"
    dni_nie = Column(String(15), nullable=True)
    codi_qr = Column(String(50), nullable=True)
    items_json = Column(Text, nullable=False)  # JSON string: [{"cn": "...", "quantitat": 1, "nom": "..."}]
    import_total = Column(Float, default=0.0)
    te_venda_creuada = Column(Boolean, default=False)
    requereix_validacio = Column(Boolean, default=False)
    validada_farmaceutic = Column(Boolean, default=False)
    estat = Column(String(40), default="Pendent")  # "Pendent validació" | "Llest per dispensar" | "Dispensada" | "Rebutjada"


def init_db():
    Base.metadata.create_all(bind=engine)