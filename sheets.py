# motor/sheets.py
# =============================================================================
# SKYPLUS — Módulo Google Sheets
# Autenticación nativa Cloud Run (google.auth.default) — sin credentials.json
# Sheet: SkyPlus_Leads | ID: 1qUVoWPBGyTgbJ3EAxuNdQM10nuq1CIiUCPu6YsngFe4
# Columnas: timestamp | correo | nombre | empresa | telefono | ancho | largo | sims_hoy
# =============================================================================

import os
import datetime
import logging

logger = logging.getLogger(__name__)

SHEET_ID   = os.getenv("SHEETS_ID", "1qUVoWPBGyTgbJ3EAxuNdQM10nuq1CIiUCPu6YsngFe4")
SHEET_NAME = "Sheet1"
CUOTA_MAX  = 3  # simulaciones máximas por usuario por día


def _get_service():
    """Retorna el servicio de Google Sheets usando credenciales nativas de Cloud Run."""
    try:
        import google.auth
        from googleapiclient.discovery import build

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return service.spreadsheets()
    except Exception as e:
        logger.error(f"Error conectando a Google Sheets: {e}")
        return None


def registrar_lead(nombre, empresa, correo, telefono, ancho, largo, comentario=""):
    """
    Registra un nuevo lead en SkyPlus_Leads.
    Retorna True si fue exitoso, False si falló.
    """
    sheet = _get_service()
    if sheet is None:
        logger.warning("Sheets no disponible — lead no registrado.")
        return False

    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fila = [[
            timestamp,
            correo.strip().lower(),
            nombre.strip(),
            empresa.strip(),
            telefono.strip() if telefono else "",
            str(ancho),
            str(largo),
            comentario.strip() if comentario else "",
            "1",   # sims_hoy — primera simulación
        ]]

        sheet.values().append(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A:I",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": fila},
        ).execute()

        logger.info(f"Lead registrado: {correo}")
        return True

    except Exception as e:
        logger.error(f"Error registrando lead {correo}: {e}")
        return False


def verificar_cuota(correo):
    """
    Verifica cuántas simulaciones ha corrido el correo hoy.
    Retorna (sims_hoy: int, permitido: bool).
    """
    sheet = _get_service()
    if sheet is None:
        # Si Sheets no está disponible, permitir por defecto
        return 0, True

    try:
        hoy = datetime.date.today().strftime("%Y-%m-%d")

        resultado = sheet.values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A:B",  # timestamp + correo
        ).execute()

        filas = resultado.get("values", [])
        correo_lower = correo.strip().lower()

        # Contar filas del día con este correo
        sims_hoy = sum(
            1 for fila in filas[1:]  # skip header
            if len(fila) >= 2
            and fila[0].startswith(hoy)
            and fila[1].strip().lower() == correo_lower
        )

        permitido = sims_hoy < CUOTA_MAX
        return sims_hoy, permitido

    except Exception as e:
        logger.error(f"Error verificando cuota para {correo}: {e}")
        return 0, True  # En caso de error, permitir


def incrementar_sim(correo):
    """
    Registra una simulación adicional para un correo ya existente hoy.
    Se usa cuando el lead ya fue registrado pero corre otra simulación el mismo día.
    """
    registrar_lead(
        nombre="",
        empresa="",
        correo=correo,
        telefono="",
        ancho="",
        largo="",
        comentario="sim_adicional",
    )
