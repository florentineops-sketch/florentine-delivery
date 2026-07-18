"""
One-time setup script:
1. Creates Google Drive folder for receipt photos
2. Adds headers to تسليمات and مرتجعات tabs
"""

import os
import json
from google.oauth2.service_account import Credentials
import gspread
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

SHEET_ID = os.environ['SHEET_ID']

PRODUCTS = ['عيش بلدي', 'محمص', 'باتون ساليه', 'حلاوة ٢٠٠', 'حلاوة ٤٠٠', 'مربى', 'تشوكو ستكس']

TASLIMAT_HEADERS = [
    'التاريخ', 'اسم المندوب', 'السلسلة', 'الفرع', 'رقم الفاتورة',
] + PRODUCTS + ['تم التسليم', 'سبب عدم التسليم', 'صورة الفاتورة']

MARTAGAAT_HEADERS = [
    'التاريخ', 'اسم المندوب', 'السلسلة', 'الفرع', 'رقم الفاتورة',
] + PRODUCTS + ['سبب المرتجع', 'صورة المرتجع']


def main():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_CREDS_JSON']),
        scopes=SCOPES
    )

    # ── Google Sheets: add headers ─────────────────────────────────────────
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID)

    # تسليمات tab
    ws_taslimat = sheet.worksheet('تسليمات')
    ws_taslimat.clear()
    ws_taslimat.append_row(TASLIMAT_HEADERS)
    print(f'✅ تسليمات headers added ({len(TASLIMAT_HEADERS)} columns)')

    # مرتجعات tab
    ws_martagaat = sheet.worksheet('مرتجعات')
    ws_martagaat.clear()
    ws_martagaat.append_row(MARTAGAAT_HEADERS)
    print(f'✅ مرتجعات headers added ({len(MARTAGAAT_HEADERS)} columns)')

    # ── Google Drive: create folder ────────────────────────────────────────
    drive = build('drive', 'v3', credentials=creds)

    folder_meta = {
        'name': 'Florentine - صور الفواتير والمرتجعات',
        'mimeType': 'application/vnd.google-apps.folder'
    }
    folder = drive.files().create(body=folder_meta, fields='id,name').execute()
    folder_id = folder['id']
    print(f'✅ Google Drive folder created: {folder["name"]} (ID: {folder_id})')
    print(f'   Save this folder ID: {folder_id}')

    # Make folder accessible (anyone with link can view)
    drive.permissions().create(
        fileId=folder_id,
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()
    print(f'✅ Folder permissions set')
    print(f'\n📁 Folder URL: https://drive.google.com/drive/folders/{folder_id}')
    print(f'\nAdd DRIVE_FOLDER_ID={folder_id} to your Railway environment variables')


if __name__ == '__main__':
    main()
