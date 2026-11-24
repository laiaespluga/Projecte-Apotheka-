import unicodedata
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import (
    SessionLocal, init_db, Usuari, Medicament,
    LotInventari, ReceptaQR, RegistreDispensacio, CatalegClinic
)
from sync_cima import (
    hash_password,
    executar_sincronitzacio_completa,
    cercar_medicaments_cima_temps_real,
    cercar_malalties_espanyol_temps_real
)

app = FastAPI(title="APOTHEKA Client Kiosk API")
init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalitzar_text(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


class UsuariCreate(BaseModel):
    dni_nie: str
    nom_complet: str
    edat: int
    password: str
    registrar_empremta: bool = False
    alergies: List[str] = []
    patologies: List[str] = []
    medicacio_habitual: List[str] = []


class LoginRequest(BaseModel):
    dni_nie: str
    password: str


class EmpremtaLoginRequest(BaseModel):
    empremta_token: str


class ItemComanda(BaseModel):
    cn: str
    quantitat: int


class ComandaRequest(BaseModel):
    dni_nie: Optional[str] = None
    items: List[ItemComanda]
    tipus_venda: str = "Sin Receta (OTC)"
    codi_qr: Optional[str] = None


@app.on_event("startup")
def startup_event():
    executar_sincronitzacio_completa()


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/cerca-clinica")
def cerca_clinica_predictiva(tipus: str, q: str = "", db: Session = Depends(get_db)):
    mapa_tipus = {
        "alergies": "alergia",
        "alergia": "alergia",
        "patologies": "patologia",
        "patologia": "patologia",
        "medicacio": "medicacio"
    }
    tipus_db = mapa_tipus.get(tipus, tipus)
    q_norm = normalitzar_text(q.strip())

    equivalencies_planeres = {
        "alergia": [
            {"nom": "Gluten / Celiaquía (Almidón de trigo)", "clau": "gluten", "origen": "Frecuente"},
            {"nom": "Lactosa (Intolerancia / Excipiente)", "clau": "lactosa", "origen": "Frecuente"},
            {"nom": "Penicilina / Amoxicilina (Antibióticos)", "clau": "penicilina", "origen": "Frecuente"},
            {"nom": "Ibuprofeno / Antiinflamatorios (AINEs)", "clau": "ibuprofeno", "origen": "Frecuente"},
            {"nom": "Aspirina (Ácido Acetilsalicílico)", "clau": "aspirina", "origen": "Frecuente"},
            {"nom": "Metamizol (Nolotil)", "clau": "metamizol", "origen": "Frecuente"},
            {"nom": "Paracetamol", "clau": "paracetamol", "origen": "Frecuente"},
            {"nom": "Fructosa / Sorbitol", "clau": "fructosa", "origen": "Frecuente"},
            {"nom": "Frutos secos / Cacahuete", "clau": "cacahuete", "origen": "Frecuente"},
            {"nom": "Soja", "clau": "soja", "origen": "Frecuente"},
        ],
        "patologia": [
            {"nom": "Embarazo", "clau": "embarazo", "origen": "Frecuente"},
            {"nom": "Lactancia materna", "clau": "lactancia", "origen": "Frecuente"},
            {"nom": "Hipertensión arterial (Tensión alta)", "clau": "hipertension", "origen": "Frecuente"},
            {"nom": "Úlcera de estómago / Gastritis", "clau": "ulcera", "origen": "Frecuente"},
            {"nom": "Arritmia cardíaca", "clau": "arritmia", "origen": "Frecuente"},
            {"nom": "Diabetes", "clau": "diabetes", "origen": "Frecuente"},
            {"nom": "Asma", "clau": "asma", "origen": "Frecuente"},
            {"nom": "Problemas de hígado (Insuficiencia hepática)", "clau": "higado", "origen": "Frecuente"},
            {"nom": "Problemas de riñón (Insuficiencia renal)", "clau": "renal", "origen": "Frecuente"},
        ]
    }

    resultats_inici = []
    resultats_conte = []
    vistos = set()

    def classificar_i_afegir(nom: str, clau: str, origen: str):
        if nom in vistos:
            return
        nom_norm = normalitzar_text(nom)
        item_dict = {"nom": nom, "clau": clau, "origen": origen}
        if not q_norm or nom_norm.startswith(q_norm) or any(p.startswith(q_norm) for p in nom_norm.split()):
            resultats_inici.append(item_dict)
            vistos.add(nom)
        elif q_norm in nom_norm:
            resultats_conte.append(item_dict)
            vistos.add(nom)

    for eq in equivalencies_planeres.get(tipus_db, []):
        classificar_i_afegir(eq["nom"], eq["clau"], eq["origen"])

    items = db.query(CatalegClinic).filter(CatalegClinic.tipus == tipus_db).all()
    for item in items:
        nom_net = item.nom_visible.replace("Intolerancia / Alergia a excipiente: ", "").replace("Alergia a principio activo: ", "")
        classificar_i_afegir(nom_net, item.clau_clinica, "BD Oficial")

    if tipus_db in ("medicacio", "alergia") and len(q_norm) >= 3:
        for med in cercar_medicaments_cima_temps_real(q.strip()):
            nom_med = med.get("nombre", "")
            if not nom_med:
                continue
            if tipus_db == "alergia":
                nom_base = nom_med.split()[0].capitalize()
                clau_med = normalitzar_text(nom_base)
                classificar_i_afegir(f"{nom_base} (Medicamento / Principio activo)", clau_med, "CIMA AEMPS")
            else:
                clau_med = normalitzar_text(nom_med.split()[0])
                classificar_i_afegir(nom_med, clau_med, "CIMA AEMPS")

    if tipus_db == "patologia" and len(q_norm) >= 3:
        for nom_pat in cercar_malalties_espanyol_temps_real(q.strip()):
            clau_pat = normalitzar_text(nom_pat.replace(" ", "_"))[:45]
            classificar_i_afegir(nom_pat, clau_pat, "MedlinePlus ES")

    return (resultats_inici + resultats_conte)[:15]


@app.post("/api/login")
def login_credencials(dades: LoginRequest, db: Session = Depends(get_db)):
    dni_net = dades.dni_nie.strip().upper()
    usuari = db.query(Usuari).filter(Usuari.dni_nie == dni_net).first()
    if not usuari or usuari.password_hash != hash_password(dades.password):
        raise HTTPException(status_code=401, detail="DNI/NIE o contraseña incorrectos.")
    return {
        "dni_nie": usuari.dni_nie,
        "nom_complet": usuari.nom_complet,
        "edat": usuari.edat,
        "alergies": usuari.alergies,
        "patologies": usuari.patologies,
        "medicacio_habitual": usuari.medicacio_habitual,
        "te_empremta": bool(usuari.empremta_token)
    }


@app.post("/api/login-empremta")
def login_empremta(dades: EmpremtaLoginRequest, db: Session = Depends(get_db)):
    usuari = db.query(Usuari).filter(Usuari.empremta_token == dades.empremta_token.strip()).first()
    if not usuari:
        raise HTTPException(status_code=404, detail="Huella dactilar no reconocida en el sistema.")
    return {
        "dni_nie": usuari.dni_nie,
        "nom_complet": usuari.nom_complet,
        "edat": usuari.edat,
        "alergies": usuari.alergies,
        "patologies": usuari.patologies,
        "medicacio_habitual": usuari.medicacio_habitual,
        "te_empremta": True
    }


@app.post("/api/usuaris")
def registrar_usuari(dades: UsuariCreate, db: Session = Depends(get_db)):
    dni_net = dades.dni_nie.strip().upper()
    if not dni_net or not dades.nom_complet.strip() or not dades.password.strip():
        raise HTTPException(status_code=400, detail="DNI/NIE, nombre completo y contraseña obligatorios.")

    if db.query(Usuari).filter(Usuari.dni_nie == dni_net).first():
        raise HTTPException(status_code=400, detail="Este DNI/NIE ya está registrado.")

    token_fp = f"FP-{dni_net}" if dades.registrar_empremta else None

    nou_usuari = Usuari(
        dni_nie=dni_net,
        nom_complet=dades.nom_complet.strip(),
        edat=dades.edat,
        password_hash=hash_password(dades.password),
        empremta_token=token_fp,
        alergies=",".join(dades.alergies),
        patologies=",".join(dades.patologies),
        medicacio_habitual=",".join(dades.medicacio_habitual),
    )
    db.add(nou_usuari)
    db.commit()
    db.refresh(nou_usuari)

    return {
        "dni_nie": nou_usuari.dni_nie,
        "nom_complet": nou_usuari.nom_complet,
        "edat": nou_usuari.edat,
        "alergies": nou_usuari.alergies,
        "patologies": nou_usuari.patologies,
        "medicacio_habitual": nou_usuari.medicacio_habitual,
        "te_empremta": bool(nou_usuari.empremta_token),
        "empremta_token": token_fp
    }


class UsuariUpdate(BaseModel):
    nom_complet: str
    edat: int
    password: Optional[str] = None
    registrar_empremta: bool = False
    alergies: List[str] = []
    patologies: List[str] = []
    medicacio_habitual: List[str] = []


@app.put("/api/usuaris/{dni_nie}")
def actualitzar_usuari(dni_nie: str, dades: UsuariUpdate, db: Session = Depends(get_db)):
    dni_net = dni_nie.strip().upper()
    usuari = db.query(Usuari).filter(Usuari.dni_nie == dni_net).first()
    if not usuari:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if not dades.nom_complet.strip():
        raise HTTPException(status_code=400, detail="El nombre completo es obligatorio.")

    usuari.nom_complet = dades.nom_complet.strip()
    usuari.edat = dades.edat
    if dades.password and dades.password.strip():
        usuari.password_hash = hash_password(dades.password.strip())

    usuari.empremta_token = f"FP-{dni_net}" if dades.registrar_empremta else None
    usuari.alergies = ",".join(dades.alergies)
    usuari.patologies = ",".join(dades.patologies)
    usuari.medicacio_habitual = ",".join(dades.medicacio_habitual)

    db.commit()
    db.refresh(usuari)

    return {
        "dni_nie": usuari.dni_nie,
        "nom_complet": usuari.nom_complet,
        "edat": usuari.edat,
        "alergies": usuari.alergies,
        "patologies": usuari.patologies,
        "medicacio_habitual": usuari.medicacio_habitual,
        "te_empremta": bool(usuari.empremta_token),
        "empremta_token": usuari.empremta_token
    }

@app.get("/api/medicaments/otc")
def llistar_medicaments_otc(db: Session = Depends(get_db)):
    meds = db.query(Medicament).filter(Medicament.requereix_recepta == False).all()
    resultat = []

    for m in meds:
        lots = db.query(LotInventari).filter(LotInventari.cn == m.cn, LotInventari.quantitat > 0).all()
        estoc_total = sum(l.quantitat for l in lots)

        cross_sell = None
        if m.recomanacio_creuada_cn:
            rec = db.query(Medicament).filter(Medicament.cn == m.recomanacio_creuada_cn).first()
            if rec:
                cross_sell = {
                    "cn": rec.cn,
                    "nom_oficial": rec.nom_oficial,
                    "preu": rec.preu,
                    "categoria": rec.categoria,
                    "url_foto": rec.url_foto
                }

        similars = db.query(Medicament).filter(
            Medicament.requereix_recepta == False,
            Medicament.categoria == m.categoria,
            Medicament.cn != m.cn
        ).limit(2).all()

        resultat.append({
            "cn": m.cn,
            "nom_oficial": m.nom_oficial,
            "principi_actiu": m.principi_actiu,
            "laboratori": m.laboratori,
            "categoria": m.categoria,
            "url_foto": m.url_foto,
            "url_prospecte": m.url_prospecte,
            "preu": m.preu,
            "limit_unitats_venda": m.limit_unitats_venda,
            "estoc_total": estoc_total,
            "recomanacio_creuada": cross_sell,
            "similars": [
                {"cn": s.cn, "nom_oficial": s.nom_oficial, "preu": s.preu, "url_foto": s.url_foto}
                for s in similars
            ]
        })
    return resultat


@app.get("/api/recepta/{codi_qr}")
def validar_qr(codi_qr: str, db: Session = Depends(get_db)):
    rec = db.query(ReceptaQR).filter(ReceptaQR.codi_qr == codi_qr.strip().upper()).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Código QR no válido o no encontrado.")
    if rec.dispensada:
        raise HTTPException(status_code=400, detail="Esta receta QR ya ha sido dispensada.")

    usuari = db.query(Usuari).filter(Usuari.dni_nie == rec.dni_nie).first()
    med = db.query(Medicament).filter(Medicament.cn == rec.cn).first()

    return {
        "codi_qr": rec.codi_qr,
        "quantitat_prescrita": rec.quantitat_prescrita,
        "usuari": {
            "dni_nie": usuari.dni_nie,
            "nom_complet": usuari.nom_complet,
            "edat": usuari.edat,
            "alergies": usuari.alergies,
            "patologies": usuari.patologies
        },
        "medicament": med
    }


@app.post("/api/dispensar")
def processar_dispensacio(comanda: ComandaRequest, db: Session = Depends(get_db)):
    usuari = None
    if comanda.dni_nie:
        usuari = db.query(Usuari).filter(Usuari.dni_nie == comanda.dni_nie.strip().upper()).first()

    alertes = []

    for item in comanda.items:
        med = db.query(Medicament).filter(Medicament.cn == item.cn).first()
        if not med:
            alertes.append(f"Medicamento {item.cn} no encontrado.")
            continue

        if item.quantitat > med.limit_unitats_venda:
            alertes.append(
                f"Límite de seguridad: máximo {med.limit_unitats_venda} unidad(es) de '{med.nom_oficial}' por operación."
            )

        if usuari and med.contraindicacions:
            contras = [normalitzar_text(c.strip()) for c in med.contraindicacions.split(",") if c.strip()]
            historial = normalitzar_text(f"{usuari.alergies},{usuari.patologies},{usuari.medicacio_habitual}")
            for contra in contras:
                if contra in historial:
                    alertes.append(
                        f"BLOQUEO CLÍNICO: '{med.nom_oficial}' ({med.principi_actiu}) es incompatible con '{contra.upper()}' registrado en el historial de {usuari.nom_complet} ({usuari.dni_nie})."
                    )

        lots = db.query(LotInventari).filter(
            LotInventari.cn == item.cn, LotInventari.quantitat > 0
        ).order_by(LotInventari.data_caducitat.asc()).all()
        if sum(l.quantitat for l in lots) < item.quantitat:
            alertes.append(f"Stock insuficiente en el robot para '{med.nom_oficial}'.")

    if alertes:
        return {"ok": False, "alertes": alertes}

    moviments = []
    for item in comanda.items:
        med = db.query(Medicament).filter(Medicament.cn == item.cn).first()
        lots = db.query(LotInventari).filter(
            LotInventari.cn == item.cn, LotInventari.quantitat > 0
        ).order_by(LotInventari.data_caducitat.asc()).all()

        pendent = item.quantitat
        for lot in lots:
            if pendent <= 0:
                break
            agafar = min(lot.quantitat, pendent)
            lot.quantitat -= agafar

            db.add(RegistreDispensacio(
                dni_nie=usuari.dni_nie if usuari else "ANÓNIMO",
                cn=med.cn,
                nom_medicament=med.nom_oficial,
                codi_lot=lot.codi_lot,
                data_caducitat=lot.data_caducitat,
                quantitat=agafar,
                tipus_venda=comanda.tipus_venda
            ))

            moviments.append({
                "nom_medicament": med.nom_oficial,
                "codi_lot": lot.codi_lot,
                "data_caducitat": lot.data_caducitat,
                "quantitat": agafar,
                "canal_robot": lot.canal_robot
            })
            pendent -= agafar

    if comanda.codi_qr:
        rec = db.query(ReceptaQR).filter(ReceptaQR.codi_qr == comanda.codi_qr.strip().upper()).first()
        if rec:
            rec.dispensada = True

    db.commit()
    return {"ok": True, "moviments": moviments}