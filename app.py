"""
Florentine Foods — نموذج التسليم والمرتجعات
Arabic driver delivery form
"""

import os
import json
import uuid
import datetime
import threading
from flask import Flask, request, jsonify, redirect
from google.oauth2.service_account import Credentials
import gspread
from googleapiclient.discovery import build

app = Flask(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

SHEET_ID   = os.environ['SHEET_ID']
FOLDER_ID  = os.environ['DRIVE_FOLDER_ID']

DRIVERS = ['محمد', 'مدحت', 'احمد رضوان', 'وليد', 'معاذ', 'انس', 'أخرى']

PRODUCTS = ['عيش بلدي', 'محمص', 'باتون ساليه', 'حلاوة ٢٠٠', 'حلاوة ٤٠٠', 'مربى', 'تشوكو ستكس']

# Chain → branches mapping (loaded from sheet)
_branches_cache = None
_cache_lock = threading.Lock()


def get_creds():
    return Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_CREDS_JSON']),
        scopes=SCOPES
    )


def get_branches():
    """Load chain→branches from الخطة اليومية tab."""
    global _branches_cache
    with _cache_lock:
        if _branches_cache:
            return _branches_cache
        creds = get_creds()
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SHEET_ID).worksheet('الخطة اليومية')
        rows = ws.get_all_values()[2:]  # skip headers

        chains = {}
        for row in rows:
            if len(row) < 2 or not row[0].strip():
                continue
            template = row[0].strip()
            branch = row[1].strip()
            if not branch:
                continue
            # Extract chain name (remove - Fresh / - Grocery)
            chain = template.split(' - ')[0].strip().title() if ' - ' in template else template.title()
            if chain not in chains:
                chains[chain] = set()
            chains[chain].add(branch)

        _branches_cache = {k: sorted(v) for k, v in sorted(chains.items())}
        return _branches_cache


def get_today_invoice(chain, branch):
    """Get today's invoice number and quantities for a branch."""
    try:
        creds = get_creds()
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SHEET_ID).worksheet('الخطة اليومية')
        rows = ws.get_all_values()[2:]

        chain_lower = chain.lower()
        branch_lower = branch.lower()

        for row in rows:
            if len(row) < 6:
                continue
            template = row[0].strip().lower()
            row_branch = row[1].strip().lower()

            if chain_lower in template and branch_lower == row_branch:
                # Read quantities (cols E, G, I, K, M, O, Q = indices 4,6,8,10,12,14,16)
                qty_indices = [4, 6, 8, 10, 12, 14, 16]
                quantities = []
                for idx in qty_indices:
                    val = row[idx].strip() if idx < len(row) else ''
                    try:
                        quantities.append(int(float(val)) if val else 0)
                    except:
                        quantities.append(0)

                # Invoice number (col R = index 17 if we add it, else empty for now)
                invoice_no = row[17].strip() if len(row) > 17 else ''

                return {
                    'invoice_no': invoice_no,
                    'quantities': dict(zip(PRODUCTS, quantities))
                }
    except Exception as e:
        print(f'Error getting invoice: {e}')
    return {'invoice_no': '', 'quantities': {p: 0 for p in PRODUCTS}}


def upload_photo(file_bytes, filename, folder_id):
    """Upload photo to Google Drive and return shareable link."""
    try:
        from googleapiclient.http import MediaInMemoryUpload
        creds = get_creds()
        drive = build('drive', 'v3', credentials=creds)

        media = MediaInMemoryUpload(file_bytes, mimetype='image/jpeg')
        file_meta = {'name': filename, 'parents': [folder_id]}
        uploaded = drive.files().create(
            body=file_meta, media_body=media, fields='id'
        ).execute()

        file_id = uploaded['id']
        drive.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        return f'https://drive.google.com/file/d/{file_id}/view'
    except Exception as e:
        print(f'Upload error: {e}')
        return ''


HTML = open('/app/form.html').read() if os.path.exists('/app/form.html') else ''


@app.route('/')
def index():
    branches = get_branches()
    chains = list(branches.keys())
    return FORM_HTML.replace('{{CHAINS_JSON}}', json.dumps(chains, ensure_ascii=False)) \
                    .replace('{{BRANCHES_JSON}}', json.dumps(branches, ensure_ascii=False)) \
                    .replace('{{DRIVERS_JSON}}', json.dumps(DRIVERS, ensure_ascii=False)) \
                    .replace('{{PRODUCTS_JSON}}', json.dumps(PRODUCTS, ensure_ascii=False))


@app.route('/branches')
def branches():
    return jsonify(get_branches())


@app.route('/invoice')
def invoice():
    chain = request.args.get('chain', '')
    branch = request.args.get('branch', '')
    return jsonify(get_today_invoice(chain, branch))


@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.form
        date = datetime.date.today().strftime('%d/%m/%Y')
        driver = data.get('driver_other') if data.get('driver') == 'أخرى' else data.get('driver')
        chain = data.get('chain')
        branch = data.get('branch')
        invoice_no = data.get('invoice_no', '')
        delivered = data.get('delivered')
        reason = data.get('reason', '')

        # Upload delivery photo
        delivery_photo_url = ''
        if 'delivery_photo' in request.files:
            f = request.files['delivery_photo']
            if f.filename:
                delivery_photo_url = upload_photo(
                    f.read(),
                    f'delivery_{chain}_{branch}_{date}.jpg',
                    FOLDER_ID
                )

        # Build تسليمات row
        taslimat_row = [date, driver, chain, branch, invoice_no]
        for product in PRODUCTS:
            if delivered == 'نعم':
                qty = data.get(f'delivered_{product}', '0')
                taslimat_row.append(qty)
            else:
                taslimat_row.append('')
        taslimat_row += [delivered, reason, delivery_photo_url]

        # Write to تسليمات
        creds = get_creds()
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SHEET_ID)
        sheet.worksheet('تسليمات').append_row(taslimat_row)

        # Handle مرتجعات if any
        has_returns = data.get('has_returns') == 'نعم'
        if has_returns:
            return_photo_url = ''
            if 'return_photo' in request.files:
                f = request.files['return_photo']
                if f.filename:
                    return_photo_url = upload_photo(
                        f.read(),
                        f'return_{chain}_{branch}_{date}.jpg',
                        FOLDER_ID
                    )

            martagaat_row = [date, driver, chain, branch, invoice_no]
            for product in PRODUCTS:
                qty = data.get(f'return_{product}', '0')
                martagaat_row.append(qty)
            return_reason = data.get('return_reason', '')
            martagaat_row += [return_reason, return_photo_url]
            sheet.worksheet('مرتجعات').append_row(martagaat_row)

        return jsonify({'status': 'ok'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


FORM_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>فلورنتين — نموذج التسليم</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f0f4f8;
      min-height: 100vh;
      padding: 20px;
      font-size: 16px;
    }
    .card {
      background: white;
      border-radius: 16px;
      padding: 24px;
      max-width: 480px;
      margin: 0 auto;
      box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    }
    h1 { font-size: 22px; color: #1a202c; margin-bottom: 4px; }
    .subtitle { color: #718096; font-size: 14px; margin-bottom: 24px; }
    .section { margin-bottom: 20px; }
    .section-title {
      font-weight: 700;
      color: #2B7A8D;
      font-size: 15px;
      margin-bottom: 12px;
      padding-bottom: 6px;
      border-bottom: 2px solid #e2e8f0;
    }
    label { display: block; color: #4a5568; font-size: 14px; margin-bottom: 4px; font-weight: 500; }
    select, input[type="text"], input[type="number"], textarea {
      width: 100%;
      padding: 12px;
      border: 1.5px solid #e2e8f0;
      border-radius: 8px;
      font-size: 15px;
      margin-bottom: 14px;
      font-family: inherit;
      color: #1a202c;
      background: #f8fafc;
    }
    select:focus, input:focus, textarea:focus {
      outline: none;
      border-color: #2B7A8D;
      background: white;
    }
    .radio-group { display: flex; gap: 12px; margin-bottom: 14px; }
    .radio-btn {
      flex: 1;
      padding: 12px;
      border: 2px solid #e2e8f0;
      border-radius: 8px;
      text-align: center;
      cursor: pointer;
      font-size: 15px;
      font-weight: 600;
      transition: all 0.2s;
      color: #4a5568;
    }
    .radio-btn.selected-yes { border-color: #276749; background: #f0fff4; color: #276749; }
    .radio-btn.selected-no  { border-color: #c53030; background: #fff5f5; color: #c53030; }
    .qty-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid #f0f0f0;
    }
    .qty-label { color: #1a202c; font-size: 14px; }
    .qty-sub { color: #718096; font-size: 12px; }
    .qty-input {
      width: 80px;
      padding: 8px;
      text-align: center;
      margin-bottom: 0;
    }
    .photo-upload {
      border: 2px dashed #cbd5e0;
      border-radius: 8px;
      padding: 20px;
      text-align: center;
      color: #718096;
      cursor: pointer;
      margin-bottom: 14px;
      transition: border-color 0.2s;
    }
    .photo-upload:hover { border-color: #2B7A8D; }
    .photo-upload input { display: none; }
    .hidden { display: none; }
    .btn {
      width: 100%;
      padding: 16px;
      background: #2B7A8D;
      color: white;
      border: none;
      border-radius: 12px;
      font-size: 17px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 8px;
      font-family: inherit;
    }
    .btn:disabled { background: #a0aec0; cursor: not-allowed; }
    .success {
      background: #f0fff4;
      border: 2px solid #276749;
      border-radius: 12px;
      padding: 24px;
      text-align: center;
      color: #276749;
      font-size: 18px;
      font-weight: 700;
      display: none;
    }
    .invoice-info {
      background: #ebf8ff;
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 13px;
      color: #2c5282;
      margin-bottom: 14px;
    }
  </style>
</head>
<body>
<div class="card">
  <h1>🚚 فلورنتين فودز</h1>
  <p class="subtitle">نموذج التسليم اليومي</p>

  <div id="formContent">

    <!-- اسم المندوب -->
    <div class="section">
      <div class="section-title">١. اسم المندوب</div>
      <label>اختر اسمك</label>
      <select id="driver" onchange="checkOther()">
        <option value="">-- اختر --</option>
      </select>
      <input type="text" id="driver_other" placeholder="اكتب اسمك" class="hidden">
    </div>

    <!-- السلسلة والفرع -->
    <div class="section">
      <div class="section-title">٢. موقع التسليم</div>
      <label>السلسلة</label>
      <select id="chain" onchange="loadBranches()">
        <option value="">-- اختر السلسلة --</option>
      </select>
      <label>الفرع</label>
      <select id="branch" onchange="loadInvoice()">
        <option value="">-- اختر الفرع --</option>
      </select>
      <div id="invoiceInfo" class="invoice-info hidden"></div>
    </div>

    <!-- هل تم التسليم -->
    <div class="section">
      <div class="section-title">٣. هل تم التسليم؟</div>
      <div class="radio-group">
        <div class="radio-btn" id="btn-yes" onclick="setDelivered('نعم')">✅ نعم</div>
        <div class="radio-btn" id="btn-no"  onclick="setDelivered('لا')">❌ لا</div>
      </div>

      <!-- If No: reason -->
      <div id="noReasonSection" class="hidden">
        <label>سبب عدم التسليم</label>
        <select id="reason">
          <option value="">-- اختر السبب --</option>
          <option>الفرع رفض الاستلام</option>
          <option>الفرع مغلق</option>
          <option>لا يوجد مسؤول</option>
          <option>مشكلة في الفاتورة</option>
          <option>أخرى</option>
        </select>
      </div>

      <!-- If Yes: quantities -->
      <div id="yesSection" class="hidden">
        <label style="margin-bottom:10px">الكميات المسلمة</label>
        <div id="quantitiesContainer"></div>

        <label>صورة فاتورة التسليم</label>
        <div class="photo-upload" onclick="document.getElementById('deliveryPhoto').click()">
          <div id="deliveryPhotoLabel">📷 اضغط لرفع صورة الفاتورة</div>
          <input type="file" id="deliveryPhoto" accept="image/*" onchange="photoSelected('deliveryPhoto','deliveryPhotoLabel')">
        </div>

        <!-- مرتجعات -->
        <div class="section-title" style="margin-top:8px">هل يوجد مرتجعات؟</div>
        <div class="radio-group">
          <div class="radio-btn" id="btn-ret-yes" onclick="setReturns('نعم')">نعم</div>
          <div class="radio-btn" id="btn-ret-no"  onclick="setReturns('لا')">لا</div>
        </div>

        <div id="returnsSection" class="hidden">
          <label style="margin-bottom:10px">كميات المرتجعات</label>
          <div id="returnsContainer"></div>
          <label>سبب المرتجع</label>
          <select id="returnReason">
            <option value="">-- اختر --</option>
            <option>منتجات منتهية الصلاحية</option>
            <option>منتجات تالفة</option>
            <option>فائض في المخزون</option>
            <option>أخرى</option>
          </select>
          <label>صورة إيصال المرتجع</label>
          <div class="photo-upload" onclick="document.getElementById('returnPhoto').click()">
            <div id="returnPhotoLabel">📷 اضغط لرفع صورة المرتجع</div>
            <input type="file" id="returnPhoto" accept="image/*" onchange="photoSelected('returnPhoto','returnPhotoLabel')">
          </div>
        </div>
      </div>
    </div>

    <button class="btn" id="submitBtn" onclick="submitForm()">إرسال التقرير</button>
  </div>

  <div class="success" id="successMsg">
    ✅ تم الإرسال بنجاح!<br>
    <span style="font-size:14px;font-weight:400;margin-top:8px;display:block">شكراً — تم تسجيل التسليم</span>
    <button class="btn" onclick="resetForm()" style="margin-top:16px;background:#276749">تسليم جديد</button>
  </div>
</div>

<script>
var CHAINS   = {{CHAINS_JSON}};
var BRANCHES = {{BRANCHES_JSON}};
var DRIVERS  = {{DRIVERS_JSON}};
var PRODUCTS = {{PRODUCTS_JSON}};
var delivered = '';
var hasReturns = '';
var invoiceData = {};

// Populate drivers
var driverSel = document.getElementById('driver');
DRIVERS.forEach(function(d) {
  var o = document.createElement('option');
  o.value = d; o.textContent = d;
  driverSel.appendChild(o);
});

// Populate chains
var chainSel = document.getElementById('chain');
CHAINS.forEach(function(c) {
  var o = document.createElement('option');
  o.value = c; o.textContent = c;
  chainSel.appendChild(o);
});

function checkOther() {
  var v = document.getElementById('driver').value;
  document.getElementById('driver_other').classList.toggle('hidden', v !== 'أخرى');
}

function loadBranches() {
  var chain = document.getElementById('chain').value;
  var branchSel = document.getElementById('branch');
  branchSel.innerHTML = '<option value="">-- اختر الفرع --</option>';
  if (!chain || !BRANCHES[chain]) return;
  BRANCHES[chain].forEach(function(b) {
    var o = document.createElement('option');
    o.value = b; o.textContent = b;
    branchSel.appendChild(o);
  });
  document.getElementById('invoiceInfo').classList.add('hidden');
}

function loadInvoice() {
  var chain  = document.getElementById('chain').value;
  var branch = document.getElementById('branch').value;
  if (!chain || !branch) return;

  fetch('/invoice?chain=' + encodeURIComponent(chain) + '&branch=' + encodeURIComponent(branch))
    .then(r => r.json())
    .then(data => {
      invoiceData = data;
      var info = document.getElementById('invoiceInfo');
      if (data.invoice_no) {
        info.textContent = '📋 رقم الفاتورة: ' + data.invoice_no;
        info.classList.remove('hidden');
      }
      buildQuantityInputs();
    });
}

function buildQuantityInputs() {
  // Delivery quantities
  var container = document.getElementById('quantitiesContainer');
  container.innerHTML = '';
  PRODUCTS.forEach(function(p) {
    var invoiced = (invoiceData.quantities && invoiceData.quantities[p]) || 0;
    var row = document.createElement('div');
    row.className = 'qty-row';
    row.innerHTML = '<div><div class="qty-label">' + p + '</div>' +
      (invoiced > 0 ? '<div class="qty-sub">مطلوب: ' + invoiced + '</div>' : '') +
      '</div><input type="number" class="qty-input" id="del_' + p + '" value="' + invoiced + '" min="0">';
    container.appendChild(row);
  });

  // Returns quantities
  var retContainer = document.getElementById('returnsContainer');
  retContainer.innerHTML = '';
  PRODUCTS.forEach(function(p) {
    var row = document.createElement('div');
    row.className = 'qty-row';
    row.innerHTML = '<div class="qty-label">' + p + '</div>' +
      '<input type="number" class="qty-input" id="ret_' + p + '" value="0" min="0">';
    retContainer.appendChild(row);
  });
}

function setDelivered(val) {
  delivered = val;
  document.getElementById('btn-yes').className = 'radio-btn' + (val==='نعم' ? ' selected-yes' : '');
  document.getElementById('btn-no').className  = 'radio-btn' + (val==='لا'  ? ' selected-no'  : '');
  document.getElementById('yesSection').classList.toggle('hidden', val !== 'نعم');
  document.getElementById('noReasonSection').classList.toggle('hidden', val !== 'لا');
  if (val === 'نعم') buildQuantityInputs();
}

function setReturns(val) {
  hasReturns = val;
  document.getElementById('btn-ret-yes').className = 'radio-btn' + (val==='نعم' ? ' selected-yes' : '');
  document.getElementById('btn-ret-no').className  = 'radio-btn' + (val==='لا'  ? ' selected-no'  : '');
  document.getElementById('returnsSection').classList.toggle('hidden', val !== 'نعم');
}

function photoSelected(inputId, labelId) {
  var file = document.getElementById(inputId).files[0];
  if (file) document.getElementById(labelId).textContent = '✅ ' + file.name;
}

function submitForm() {
  var driver = document.getElementById('driver').value;
  var driverOther = document.getElementById('driver_other').value;
  var chain = document.getElementById('chain').value;
  var branch = document.getElementById('branch').value;

  if (!driver || !chain || !branch || !delivered) {
    alert('يرجى تعبئة جميع الحقول المطلوبة');
    return;
  }
  if (driver === 'أخرى' && !driverOther) {
    alert('يرجى كتابة اسمك');
    return;
  }

  var btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = '⏳ جاري الإرسال...';

  var formData = new FormData();
  formData.append('driver', driver === 'أخرى' ? driverOther : driver);
  formData.append('chain', chain);
  formData.append('branch', branch);
  formData.append('invoice_no', invoiceData.invoice_no || '');
  formData.append('delivered', delivered);
  formData.append('reason', document.getElementById('reason') ? document.getElementById('reason').value : '');
  formData.append('has_returns', hasReturns);
  formData.append('return_reason', document.getElementById('returnReason') ? document.getElementById('returnReason').value : '');

  PRODUCTS.forEach(function(p) {
    var delEl = document.getElementById('del_' + p);
    var retEl = document.getElementById('ret_' + p);
    formData.append('delivered_' + p, delEl ? delEl.value : '0');
    formData.append('return_' + p, retEl ? retEl.value : '0');
  });

  var delPhoto = document.getElementById('deliveryPhoto');
  if (delPhoto && delPhoto.files[0]) formData.append('delivery_photo', delPhoto.files[0]);

  var retPhoto = document.getElementById('returnPhoto');
  if (retPhoto && retPhoto.files[0]) formData.append('return_photo', retPhoto.files[0]);

  fetch('/submit', {method: 'POST', body: formData})
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        document.getElementById('formContent').style.display = 'none';
        document.getElementById('successMsg').style.display = 'block';
      } else {
        alert('خطأ: ' + data.message);
        btn.disabled = false;
        btn.textContent = 'إرسال التقرير';
      }
    })
    .catch(err => {
      alert('خطأ في الاتصال');
      btn.disabled = false;
      btn.textContent = 'إرسال التقرير';
    });
}

function resetForm() {
  document.getElementById('formContent').style.display = 'block';
  document.getElementById('successMsg').style.display = 'none';
  document.getElementById('driver').value = '';
  document.getElementById('chain').value = '';
  document.getElementById('branch').innerHTML = '<option value="">-- اختر الفرع --</option>';
  document.getElementById('invoiceInfo').classList.add('hidden');
  delivered = '';
  hasReturns = '';
  setDelivered('');
  document.getElementById('submitBtn').disabled = false;
  document.getElementById('submitBtn').textContent = 'إرسال التقرير';
}
</script>
</body>
</html>"""


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
