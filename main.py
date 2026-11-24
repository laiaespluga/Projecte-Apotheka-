import json
import unicodedata
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import (
    SessionLocal, init_db, Usuari, Medicament,
    LotInventari, ReceptaQR, RegistreDispensacio, CatalegClinic, ComandaCua
)
from sync_cima import (
    hash_password,
    executar_sincronitzacio_completa,
    cercar_medicaments_cima_temps_real,
    cercar_malalties_espanyol_temps_real
)

app = FastAPI(title="APOTHEKA Client Kiosk & Pharmacy API")
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
    te_venda_creuada: bool = False


class ReabastirLotRequest(BaseModel):
    quantitat_afegida: int = 20
    dies_caducitat: int = 365


@app.on_event("startup")
def startup_event():
    executar_sincronitzacio_completa()


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/farmacia")
def panell_farmacia():
    return FileResponse("static/farmacia.html")


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
def llistar_medicaments_otc(dni_nie: Optional[str] = None, db: Session = Depends(get_db)):
    usuari = None
    if dni_nie:
        usuari = db.query(Usuari).filter(Usuari.dni_nie == dni_nie.strip().upper()).first()

    historial_norm = ""
    if usuari:
        historial_norm = normalitzar_text(
            f"{usuari.alergies},{usuari.patologies},{usuari.medicacio_habitual}"
        )

    def es_compatible(med_obj: Medicament) -> bool:
        if not historial_norm or not med_obj.contraindicacions:
            return True
        contras = [normalitzar_text(c.strip()) for c in med_obj.contraindicacions.split(",") if c.strip()]
        return not any(c in historial_norm for c in contras)

    meds = db.query(Medicament).filter(Medicament.requereix_recepta == False).all()
    resultat = []

    for m in meds:
        lots = db.query(LotInventari).filter(LotInventari.cn == m.cn, LotInventari.quantitat > 0).all()
        estoc_total = sum(l.quantitat for l in lots)

        cross_sell = None
        if m.recomanacio_creuada_cn:
            rec = db.query(Medicament).filter(Medicament.cn == m.recomanacio_creuada_cn).first()
            if rec and es_compatible(rec):
                rec_lots = db.query(LotInventari).filter(LotInventari.cn == rec.cn, LotInventari.quantitat > 0).all()
                if sum(l.quantitat for l in rec_lots) > 0:
                    cross_sell = {
                        "cn": rec.cn,
                        "nom_oficial": rec.nom_oficial,
                        "preu": rec.preu,
                        "categoria": rec.categoria,
                        "url_foto": rec.url_foto
                    }

        candidates_similars = db.query(Medicament).filter(
            Medicament.requereix_recepta == False,
            Medicament.categoria == m.categoria,
            Medicament.cn != m.cn
        ).all()

        similars_segurs = []
        for s in candidates_similars:
            if not es_compatible(s):
                continue
            s_lots = db.query(LotInventari).filter(LotInventari.cn == s.cn, LotInventari.quantitat > 0).all()
            if sum(l.quantitat for l in s_lots) > 0:
                similars_segurs.append({
                    "cn": s.cn,
                    "nom_oficial": s.nom_oficial,
                    "principi_actiu": s.principi_actiu,
                    "preu": s.preu,
                    "url_foto": s.url_foto
                })

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
            "contraindicacions": m.contraindicacions,
            "recomanacio_creuada": cross_sell,
            "similars": similars_segurs[:3]
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
    detall_items = []
    import_total = 0.0
    requereix_validacio = bool(comanda.codi_qr)

    for item in comanda.items:
        med = db.query(Medicament).filter(Medicament.cn == item.cn).first()
        if not med:
            alertes.append(f"Medicamento {item.cn} no encontrado.")
            continue

        if med.requereix_recepta:
            requereix_validacio = True

        import_total += med.preu * item.quantitat
        detall_items.append({"cn": med.cn, "quantitat": item.quantitat, "nom": med.nom_oficial})

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

    origen_cua = "Recepta QR Exterior" if comanda.codi_qr else "Taulell / 24-7"
    estat_inicial = "Pendent validació" if requereix_validacio else "Llest per dispensar"

    nova_cua = ComandaCua(
        origen=origen_cua,
        dni_nie=usuari.dni_nie if usuari else "ANÓNIMO",
        codi_qr=comanda.codi_qr.strip().upper() if comanda.codi_qr else None,
        items_json=json.dumps(detall_items, ensure_ascii=False),
        import_total=round(import_total, 2),
        te_venda_creuada=comanda.te_venda_creuada,
        requereix_validacio=requereix_validacio,
        validada_farmaceutic=not requereix_validacio,
        estat=estat_inicial
    )
    db.add(nova_cua)

    db.commit()
    return {"ok": True, "moviments": moviments, "comanda_cua_id": nova_cua.id}


# --- ENDPOINTS PANELL INTERN DE FARMÀCIA ---

@app.get("/api/farmacia/estat")
def obtenir_estat_farmacia(db: Session = Depends(get_db)):
    cua_rows = db.query(ComandaCua).order_by(ComandaCua.id.desc()).all()
    cua_list = []
    for c in cua_rows:
        try:
            items = json.loads(c.items_json)
        except Exception:
            items = []
        usuari = db.query(Usuari).filter(Usuari.dni_nie == c.dni_nie).first() if c.dni_nie and c.dni_nie != "ANÓNIMO" else None
        cua_list.append({
            "id": c.id,
            "data_hora": c.data_hora.strftime("%H:%M:%S") if c.data_hora else "",
            "origen": c.origen,
            "dni_nie": c.dni_nie or "ANÓNIMO",
            "pacient_nom": usuari.nom_complet if usuari else "Usuari anònim",
            "alergies_pacient": usuari.alergies if usuari else "",
            "patologies_pacient": usuari.patologies if usuari else "",
            "medicacio_pacient": usuari.medicacio_habitual if usuari else "",
            "codi_qr": c.codi_qr,
            "items": items,
            "import_total": c.import_total,
            "te_venda_creuada": c.te_venda_creuada,
            "requereix_validacio": c.requereix_validacio,
            "validada_farmaceutic": c.validada_farmaceutic,
            "estat": c.estat
        })

    lots_rows = db.query(LotInventari).order_by(LotInventari.data_caducitat.asc()).all()
    avui_str = datetime.now().strftime("%Y-%m-%d")
    limit_30d_str = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    lots_list = []
    for l in lots_rows:
        med = db.query(Medicament).filter(Medicament.cn == l.cn).first()
        proper_caducar = l.data_caducitat <= limit_30d_str
        baix_estoc = l.quantitat <= 5
        lots_list.append({
            "id": l.id,
            "cn": l.cn,
            "nom_medicament": med.nom_oficial if med else l.cn,
            "categoria": med.categoria if med else "",
            "codi_lot": l.codi_lot,
            "data_caducitat": l.data_caducitat,
            "quantitat": l.quantitat,
            "canal_robot": l.canal_robot,
            "proper_caducar": proper_caducar,
            "baix_estoc": baix_estoc
        })

    dispensacions = db.query(RegistreDispensacio).all()
    qr_tramitades = sum(1 for d in dispensacions if "QR" in (d.tipus_venda or ""))
    vendes_24_7 = sum(1 for d in dispensacions if "QR" not in (d.tipus_venda or ""))
    alertes_actives = sum(1 for l in lots_list if l["proper_caducar"] or l["baix_estoc"]) + sum(
        1 for c in cua_rows if c.requereix_validacio and not c.validada_farmaceutic
    )
    comandes_cross_sell = sum(1 for c in cua_rows if c.te_venda_creuada)
    facturacio_registrada = sum(c.import_total for c in cua_rows if c.estat == "Dispensada")

    return {
        "cua": cua_list,
        "lots_fefo": lots_list,
        "kpis": {
            "receptes_qr_tramitades": qr_tramitades,
            "vendes_24_7": vendes_24_7,
            "alertes_preventives_actives": alertes_actives,
            "comandes_venda_creuada": comandes_cross_sell,
            "facturacio_registrada": round(facturacio_registrada, 2),
            "data_referencia": avui_str
        }
    }


@app.post("/api/farmacia/cua/{comanda_id}/validar")
def validar_comanda_farmacia(comanda_id: int, db: Session = Depends(get_db)):
    c = db.query(ComandaCua).filter(ComandaCua.id == comanda_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Comanda no trobada.")
    c.validada_farmaceutic = True
    c.estat = "Llest per dispensar"
    db.commit()
    return {"ok": True, "estat": c.estat}


@app.post("/api/farmacia/cua/{comanda_id}/dispensar")
def dispensar_comanda_farmacia(comanda_id: int, db: Session = Depends(get_db)):
    c = db.query(ComandaCua).filter(ComandaCua.id == comanda_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Comanda no trobada.")
    if c.requereix_validacio and not c.validada_farmaceutic:
        raise HTTPException(status_code=400, detail="Cal validar la dispensació abans de lliurar en guàrdia.")
    c.estat = "Dispensada"
    db.commit()
    return {"ok": True, "estat": c.estat}


@app.post("/api/farmacia/cua/{comanda_id}/rebutjar")
def rebutjar_comanda_farmacia(comanda_id: int, db: Session = Depends(get_db)):
    c = db.query(ComandaCua).filter(ComandaCua.id == comanda_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Comanda no trobada.")
    c.estat = "Rebutjada"
    db.commit()
    return {"ok": True, "estat": c.estat}


@app.post("/api/farmacia/lots/{lot_id}/reabastir")
def reabastir_lot_fefo(lot_id: int, dades: ReabastirLotRequest, db: Session = Depends(get_db)):
    lot = db.query(LotInventari).filter(LotInventari.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot no trobat.")
    lot.quantitat += max(1, dades.quantitat_afegida)
    nova_cad = datetime.now() + timedelta(days=max(30, dades.dies_caducitat))
    lot.data_caducitat = nova_cad.strftime("%Y-%m-%d")
    db.commit()
    return {"ok": True, "quantitat": lot.quantitat, "data_caducitat": lot.data_caducitat}