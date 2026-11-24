import re
import hashlib
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timedelta
from models import (
    init_db, SessionLocal, Medicament, LotInventari,
    Usuari, ReceptaQR, CatalegClinic
)

CIMA_BASE = "https://cima.aemps.es/cima/rest"
MEDLINE_ES_API = "https://wsearch.nlm.nih.gov/ws/query"

NREGISTROS_QUIOSC = [
    ("60514", "Dolor y Fiebre", 7.95, 2, "ulcera,embarazo,hipertension", False),
    ("52010", "Dolor y Fiebre", 4.50, 2, "higado,hepatica", False),
    ("82921", "Digestivo", 3.50, 2, "", False),
    ("58329", "Digestivo", 8.90, 2, "", False),
    ("49269", "Resfriado y Gripe", 9.80, 2, "hipertension,glaucoma", False),
    ("63696", "Alergias", 4.90, 2, "embarazo", False),
    ("70039", "Prescripción Médica", 4.20, 1, "ulcera,embarazo,hipertension", True),
    ("62437", "Prescripción Médica", 5.10, 1, "penicilina,amoxicilina", True),
]


def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode("utf-8")).hexdigest()


def netejar_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def descarregar_patologies_espanyol_api(db):
    print("🌐 Descargando catálogo oficial de enfermedades en español (MedlinePlus NIH API)...")
    consultes = [
        "arritmia", "hipertension", "diabetes", "asma", "ulcera", "gastritis",
        "hepatitis", "higado", "renal", "epilepsia", "migrana", "glaucoma", "anemia",
        "artritis", "tiroides", "embarazo", "lactancia", "corazon", "bronquitis",
        "dermatitis", "psoriasis", "crohn", "colitis", "trombosis", "colesterol",
        "osteoporosis", "parkinson", "alzheimer", "ansiedad", "depresion", "insomnio",
        "alergia", "infeccion", "cancer", "coagulacion", "prostata", "vertigo"
    ]
    vistos = set()
    total = 0

    for terme in consultes:
        try:
            resp = requests.get(
                MEDLINE_ES_API,
                params={"db": "healthTopicsSpanish", "term": terme, "retmax": 25},
                timeout=8
            )
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for doc in root.findall(".//document"):
                    title_el = doc.find("./content[@name='title']")
                    if title_el is not None:
                        nom_net = netejar_html("".join(title_el.itertext()))
                        if nom_net and nom_net not in vistos:
                            clau = nom_net.lower().replace(",", "").replace(" ", "_")[:50]
                            db.add(CatalegClinic(tipus="patologia", nom_visible=nom_net, clau_clinica=clau))
                            vistos.add(nom_net)
                            total += 1
        except Exception as e:
            print(f"  ⚠️ Error MedlinePlus ({terme}): {e}")

    db.commit()
    print(f"  ✅ {total} patologías oficiales en español indexadas.")


def descarregar_medicaments_i_alergies_cima(db):
    print("🌐 Descargando medicamentos, principios activos y excipientes desde CIMA (AEMPS)...")
    vistos_meds = set()
    vistos_alergies = set()
    total_meds = 0
    total_alergies = 0

    for pagina in range(1, 13):
        try:
            resp = requests.get(
                f"{CIMA_BASE}/medicamentos",
                params={"comerc": 1, "pagina": pagina},
                timeout=8
            )
            if resp.status_code == 200:
                for med in resp.json().get("resultados", []):
                    nom_oficial = med.get("nombre", "").strip()
                    nreg = med.get("nregistro", "")
                    if nom_oficial and nom_oficial not in vistos_meds:
                        clau_med = nom_oficial.split()[0].lower()
                        db.add(CatalegClinic(tipus="medicacio", nom_visible=nom_oficial, clau_clinica=clau_med))
                        vistos_meds.add(nom_oficial)
                        total_meds += 1

                    if nreg and total_meds % 3 == 0:
                        det = requests.get(f"{CIMA_BASE}/medicamento", params={"nregistro": nreg}, timeout=6).json()
                        for pa in det.get("principiosActivos", []):
                            nom_pa = pa.get("nombre", "").strip().capitalize()
                            if nom_pa and nom_pa not in vistos_alergies:
                                db.add(CatalegClinic(
                                    tipus="alergia",
                                    nom_visible=f"Alergia a principio activo: {nom_pa}",
                                    clau_clinica=nom_pa.lower().split()[0]
                                ))
                                vistos_alergies.add(nom_pa)
                                total_alergies += 1
                        for exc in det.get("excipientes", []):
                            nom_exc = exc.get("nombre", "").strip().capitalize()
                            if nom_exc and nom_exc not in vistos_alergies:
                                db.add(CatalegClinic(
                                    tipus="alergia",
                                    nom_visible=f"Intolerancia / Alergia a excipiente: {nom_exc}",
                                    clau_clinica=nom_exc.lower().split()[0]
                                ))
                                vistos_alergies.add(nom_exc)
                                total_alergies += 1
        except Exception:
            continue

    db.commit()
    print(f"  ✅ {total_meds} medicamentos oficiales en español indexados.")
    print(f"  ✅ {total_alergies} principios activos y excipientes indexados para alergias.")


def cercar_medicaments_cima_temps_real(query: str, limit: int = 12) -> list:
    try:
        resp = requests.get(f"{CIMA_BASE}/medicamentos", params={"nombre": query}, timeout=4)
        if resp.status_code == 200:
            return resp.json().get("resultados", [])[:limit]
    except Exception:
        pass
    return []


def cercar_malalties_espanyol_temps_real(query: str, limit: int = 12) -> list:
    resultats = []
    try:
        resp = requests.get(
            MEDLINE_ES_API,
            params={"db": "healthTopicsSpanish", "term": query, "retmax": limit},
            timeout=4
        )
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            for doc in root.findall(".//document"):
                title_el = doc.find("./content[@name='title']")
                if title_el is not None:
                    nom_net = netejar_html("".join(title_el.itertext()))
                    if nom_net:
                        resultats.append(nom_net)
    except Exception:
        pass
    return resultats


def executar_sincronitzacio_completa():
    print("🔄 Inicializando base de datos APOTHEKA en español...")
    init_db()
    db = SessionLocal()

    if db.query(CatalegClinic).count() == 0:
        descarregar_patologies_espanyol_api(db)
        descarregar_medicaments_i_alergies_cima(db)

    if db.query(Medicament).count() == 0:
        avui = datetime.now()
        print("🤖 Descargando fichas oficiales para el robot desde CIMA AEMPS...")

        for idx, (nreg, cat, preu, limit, contra, req_recepta) in enumerate(NREGISTROS_QUIOSC):
            resp = requests.get(f"{CIMA_BASE}/medicamento", params={"nregistro": nreg}, timeout=10)
            if resp.status_code == 200:
                info = resp.json()
                nom_oficial = info.get("nombre", f"Medicamento {nreg}")
                laboratori = info.get("labtitular", "Laboratorio Autorizado")
                principios = [p["nombre"] for p in info.get("principiosActivos", [])]
                principi_str = ", ".join(principios) if principios else nom_oficial.split()[0]
                fotos = info.get("fotos", [])
                url_foto = fotos[0]["url"] if fotos else ""
                docs = info.get("docs", [])
                url_prospecte = next((d["urlHtml"] for d in docs if d.get("tipo") == 2 and d.get("urlHtml")), "")

                db.add(Medicament(
                    cn=nreg, nregistro=nreg, nom_oficial=nom_oficial,
                    principi_actiu=principi_str, laboratori=laboratori,
                    categoria=cat, requereix_recepta=req_recepta,
                    url_foto=url_foto, url_prospecte=url_prospecte,
                    preu=preu, limit_unitats_venda=limit,
                    contraindicacions=contra
                ))

                db.add_all([
                    LotInventari(
                        cn=nreg, codi_lot=f"LOT-26-{100+idx}",
                        data_caducitat=(avui + timedelta(days=30 + idx * 5)).strftime("%Y-%m-%d"),
                        quantitat=4, canal_robot=f"A-{idx+1:02d}"
                    ),
                    LotInventari(
                        cn=nreg, codi_lot=f"LOT-27-{500+idx}",
                        data_caducitat=(avui + timedelta(days=400 + idx * 10)).strftime("%Y-%m-%d"),
                        quantitat=20, canal_robot=f"B-{idx+1:02d}"
                    )
                ])
                print(f"  ✅ [CIMA] {nreg} - {nom_oficial}")

        db.commit()

        vincles = {"60514": "82921", "58329": "82921", "49269": "52010", "63696": "52010"}
        for orig, dest in vincles.items():
            m = db.query(Medicament).filter_by(cn=orig).first()
            if m:
                m.recomanacio_creuada_cn = dest
        db.commit()

    if db.query(Usuari).count() == 0:
        db.add_all([
            Usuari(dni_nie="12345678A", nom_complet="Marc Puig Soler", edat=42, password_hash=hash_password("1234"), empremta_token="FP-12345678A", alergies="penicilina,amoxicilina", patologies="", medicacio_habitual=""),
            Usuari(dni_nie="87654321B", nom_complet="Laia Martínez Roca", edat=33, password_hash=hash_password("1234"), empremta_token="FP-87654321B", alergies="", patologies="embarazo", medicacio_habitual=""),
            Usuari(dni_nie="X1234567C", nom_complet="Josep Gómez Vidal", edat=67, password_hash=hash_password("1234"), empremta_token="FP-X1234567C", alergies="", patologies="hipertension,ulcera,arritmia", medicacio_habitual="enalapril"),
            Usuari(dni_nie="45678912D", nom_complet="Clara Serra Pons", edat=26, password_hash=hash_password("1234"), empremta_token="FP-45678912D", alergies="", patologies="", medicacio_habitual=""),
        ])
        db.commit()

    if db.query(ReceptaQR).count() == 0:
        db.add_all([
            ReceptaQR(codi_qr="QR-APOTHEKA-01", dni_nie="12345678A", cn="62437", quantitat_prescrita=1, dispensada=False),
            ReceptaQR(codi_qr="QR-APOTHEKA-02", dni_nie="45678912D", cn="70039", quantitat_prescrita=1, dispensada=False),
            ReceptaQR(codi_qr="QR-APOTHEKA-03", dni_nie="X1234567C", cn="70039", quantitat_prescrita=1, dispensada=False),
        ])
        db.commit()

    db.close()
    print("🎉 Base de datos APOTHEKA unificada en español y lista!")


if __name__ == "__main__":
    executar_sincronitzacio_completa()