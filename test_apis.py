import requests
import xml.etree.ElementTree as ET
import json

CIMA_BASE = "https://cima.aemps.es/cima/rest"
MEDLINE_ES_API = "https://wsearch.nlm.nih.gov/ws/query"


def comprovar_cima_castella():
    print("==========================================================")
    print("1. API CIMA (AEMPS) - MEDICAMENTS, PRINCIPIS I EXCIPIENTS")
    print("==========================================================")
    resp = requests.get(f"{CIMA_BASE}/medicamentos", params={"comerc": 1, "pagina": 1}, timeout=10)
    resp.raise_for_status()
    dades = resp.json()
    print(f"Total medicaments comercialitzats a Espanya: {dades.get('totalFilas')}")

    # Agafem un medicament de la llista i consultem la fitxa completa
    primer = dades["resultados"][1]
    nreg = primer["nregistro"]
    detall = requests.get(f"{CIMA_BASE}/medicamento", params={"nregistro": nreg}, timeout=10).json()

    print(f"Exemple fitxa completa ({nreg}):")
    print(json.dumps({
        "nombre": detall.get("nombre"),
        "principiosActivos": [p["nombre"] for p in detall.get("principiosActivos", [])],
        "excipientes": [e["nombre"] for e in detall.get("excipientes", [])],
        "receta": detall.get("receta")
    }, indent=2, ensure_ascii=False))


def comprovar_medline_espanyol():
    print("\n==========================================================")
    print("2. API MEDLINEPLUS EN ESPAÑOL (NIH) - MALALTIES I CONDICIONS")
    print("==========================================================")
    termes_prova = ["arritmia", "embarazo", "diabetes"]

    for terme in termes_prova:
        resp = requests.get(
            MEDLINE_ES_API,
            params={"db": "healthTopicsSpanish", "term": terme, "retmax": 4},
            timeout=10
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        total = root.findtext(".//count", default="0")
        print(f"\nCerca '{terme}' -> Total resultats oficials en castellà: {total}")

        for doc in root.findall(".//document"):
            title_el = doc.find("./content[@name='title']")
            if title_el is not None:
                nom_net = "".join(title_el.itertext()).strip()
                print(f"  - {nom_net}")


if __name__ == "__main__":
    comprovar_cima_castella()
    comprovar_medline_espanyol()
    print("\n✅ Comprovació finalitzada! Tot en castellà i estructurat.")