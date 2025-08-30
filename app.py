from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, abort
import sqlite3, os, secrets, csv
from werkzeug.utils import secure_filename
from datetime import datetime

# CONFIGURATION
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
DATABASE = os.path.join(os.path.dirname(__file__), 'concurso.db')
CSV_FILE = os.path.join(os.path.dirname(__file__), 'inscripciones.csv')
ADMIN_PASSWORD = 'carn2admin'  # CHANGE before production
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.secret_key = secrets.token_hex(16)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Database helpers ---
def get_db_conn():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS inscripciones (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, matricula TEXT NOT NULL, pseudonimo TEXT NOT NULL, codigo TEXT NOT NULL UNIQUE, fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, inscripcion_id INTEGER, codigo TEXT, filename TEXT, filetype TEXT, fecha DATETIME DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(inscripcion_id) REFERENCES inscripciones(id))")
    conn.commit()
    conn.close()
    # ensure CSV exists with header
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id','nombre','matricula','pseudonimo','codigo','fecha_registro'])

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_codigo(prefix='CARN2-', length=8):
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    while True:
        code = prefix + ''.join(secrets.choice(alphabet) for _ in range(length))
        conn = get_db_conn(); cur = conn.cursor()
        cur.execute('SELECT id FROM inscripciones WHERE codigo = ?', (code,))
        r = cur.fetchone(); conn.close()
        if not r:
            return code

def append_csv(insc):
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([insc['id'], insc['nombre'], insc['matricula'], insc['pseudonimo'], insc['codigo'], insc['fecha_registro']])

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/inscripcion', methods=['GET','POST'])
def inscripcion():
    if request.method == 'POST':
        nombre = request.form.get('nombre','').strip()
        matricula = request.form.get('matricula','').strip()
        pseudonimo = request.form.get('seudonimo','').strip()
        if not nombre or not matricula or not pseudonimo:
            flash('Complete todos los campos obligatorios.', 'danger')
            return redirect(url_for('inscripcion'))
        codigo = generate_codigo()
        conn = get_db_conn(); cur = conn.cursor()
        cur.execute('INSERT INTO inscripciones (nombre,matricula,pseudonimo,codigo) VALUES (?,?,?,?)', (nombre,matricula,pseudonimo,codigo))
        conn.commit()
        insc_id = cur.lastrowid
        cur.execute('SELECT fecha_registro FROM inscripciones WHERE id = ?', (insc_id,))
        row = cur.fetchone()
        fecha = row['fecha_registro'] if row and 'fecha_registro' in row.keys() else datetime.utcnow().isoformat()
        conn.close()
        insc = {'id': insc_id, 'nombre': nombre, 'matricula': matricula, 'pseudonimo': pseudonimo, 'codigo': codigo, 'fecha_registro': fecha}
        append_csv(insc)
        return render_template('inscripcion.html', codigo=codigo, nombre=nombre, pseudonimo=pseudonimo)
    return render_template('inscripcion.html')

@app.route('/presentacion', methods=['GET','POST'])
def presentacion():
    mensaje = None; exito = False
    if request.method == 'POST':
        codigo = request.form.get('codigo','').strip()
        if not codigo:
            mensaje = 'Debes ingresar tu código.'; exito = False; return render_template('presentacion.html', mensaje=mensaje, exito=exito)
        conn = get_db_conn(); cur = conn.cursor()
        cur.execute('SELECT id FROM inscripciones WHERE codigo = ?', (codigo,))
        r = cur.fetchone()
        if not r:
            conn.close(); mensaje = 'Código no encontrado.'; exito = False; return render_template('presentacion.html', mensaje=mensaje, exito=exito)
        insc_id = r['id']
        proyecto = request.files.get('proyecto'); declaracion = request.files.get('declaracion')
        if not proyecto or proyecto.filename == '' or not allowed_file(proyecto.filename):
            mensaje = 'Subí el archivo PDF del proyecto.'; exito = False; return render_template('presentacion.html', mensaje=mensaje, exito=exito)
        if not declaracion or declaracion.filename == '' or not allowed_file(declaracion.filename):
            mensaje = 'Subí la declaración jurada en PDF.'; exito = False; return render_template('presentacion.html', mensaje=mensaje, exito=exito)
        save_dir = os.path.join(app.config['UPLOAD_FOLDER'], codigo)
        os.makedirs(save_dir, exist_ok=True)
        pfn = secure_filename('proyecto_' + proyecto.filename)
        dfn = secure_filename('declaracion_' + declaracion.filename)
        proyecto.save(os.path.join(save_dir, pfn))
        declaracion.save(os.path.join(save_dir, dfn))
        fecha = datetime.utcnow().isoformat()
        cur.execute('INSERT INTO submissions (inscripcion_id,codigo,filename,filetype,fecha) VALUES (?,?,?,?,?)', (insc_id,codigo,pfn,'proyecto',fecha))
        cur.execute('INSERT INTO submissions (inscripcion_id,codigo,filename,filetype,fecha) VALUES (?,?,?,?,?)', (insc_id,codigo,dfn,'declaracion',fecha))
        conn.commit(); conn.close()
        mensaje = f'Archivos subidos correctamente. Tu envío queda identificado por el código: {codigo}'; exito = True
    return render_template('presentacion.html', mensaje=mensaje, exito=exito)

from flask import session
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        pwd = request.form.get('password','')
        if pwd == ADMIN_PASSWORD:
            session['admin'] = True; return redirect(url_for('admin_panel'))
        flash('Password incorrecto', 'danger'); return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return wrapped

@app.route('/admin/panel')
@admin_required
def admin_panel():
    conn = get_db_conn(); cur = conn.cursor()
    cur.execute('SELECT i.id,i.fecha_registro,i.pseudonimo,i.codigo,i.matricula, COUNT(s.id) as files FROM inscripciones i LEFT JOIN submissions s ON i.id=s.inscripcion_id GROUP BY i.id ORDER BY i.id DESC')
    rows = cur.fetchall(); conn.close()
    return render_template('admin_panel.html', participants=rows)

@app.route('/admin/download/<codigo>/<filename>')
@admin_required
def admin_download(codigo, filename):
    folder = os.path.join(app.config['UPLOAD_FOLDER'], codigo)
    return send_from_directory(folder, filename, as_attachment=True)

@app.route('/admin/registry')
@admin_required
def admin_registry():
    conn = get_db_conn(); cur = conn.cursor()
    cur.execute('SELECT id,fecha_registro,nombre,matricula,pseudonimo,codigo FROM inscripciones ORDER BY id DESC')
    rows = cur.fetchall(); conn.close()
    return render_template('admin_registry.html', rows=rows)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
