from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash, g, jsonify
import os
import secrets
import threading
import uuid
import psycopg2
import psycopg2.extras  # CORRECCIÓN CRÍTICA: Importación explícita para que DictCursor funcione
import stripe
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from core_extractor import procesar_video_partitura

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "clave_secreta_super_segura_para_el_negocio")

# Configuración para que la sesión recuerde al usuario en su navegador por 30 días
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =====================================================================
# CONFIGURACIÓN DE LAS BASES DE DATOS (POSTGRESQL / NEON)
# =====================================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/sheetmusic_db")

def obtener_conexion_db():
    # CORRECCIÓN CRÍTICA: Forzar sslmode='require' para evitar que Neon rechace la conexión segura
    if "localhost" not in DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    else:
        conn = psycopg2.connect(DATABASE_URL)
    return conn

def inicializar_base_de_datos():
    conn = obtener_conexion_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_pro INT DEFAULT 0,
            creditos INT DEFAULT 3
        )
    ''')
    # NUEVO: los créditos gratuitos ya NO dependen de la cuenta (columna 'creditos'
    # de arriba, que se deja sin usar para no romper instalaciones existentes).
    # Se asignan por dispositivo (cookie persistente), así que registrarse o no
    # registrarse no da ni quita créditos — evita el abuso de crear cuentas infinitas.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS creditos_gratis (
            device_id TEXT PRIMARY KEY,
            creditos INT DEFAULT 3
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

inicializar_base_de_datos()

# =====================================================================
# CRÉDITOS GRATUITOS POR DISPOSITIVO (no por cuenta)
# =====================================================================
COOKIE_DEVICE_ID = "device_id"
DOS_ANOS_SEGUNDOS = 60 * 60 * 24 * 365 * 2

@app.before_request
def preparar_device_id():
    # Si el navegador no trae cookie de dispositivo, generamos una nueva.
    # No la guardamos en la cookie todavía: eso se hace en after_request,
    # una vez que ya tenemos la respuesta a la que añadirle el set_cookie.
    if request.cookies.get(COOKIE_DEVICE_ID):
        g.device_id = request.cookies.get(COOKIE_DEVICE_ID)
        g.device_id_es_nuevo = False
    else:
        g.device_id = secrets.token_hex(16)
        g.device_id_es_nuevo = True

@app.after_request
def guardar_device_id(response):
    if getattr(g, "device_id_es_nuevo", False):
        response.set_cookie(
            COOKIE_DEVICE_ID, g.device_id,
            max_age=DOS_ANOS_SEGUNDOS, httponly=True, samesite="Lax"
        )
    return response

def obtener_creditos_gratis(device_id):
    conn = obtener_conexion_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT creditos FROM creditos_gratis WHERE device_id = %s', (device_id,))
    fila = cursor.fetchone()
    if fila is None:
        cursor.execute(
            'INSERT INTO creditos_gratis (device_id, creditos) VALUES (%s, %s) ON CONFLICT (device_id) DO NOTHING',
            (device_id, 3)
        )
        conn.commit()
        creditos = 3
    else:
        creditos = fila['creditos']
    cursor.close()
    conn.close()
    return creditos

def descontar_credito_gratis(device_id):
    conn = obtener_conexion_db()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE creditos_gratis SET creditos = creditos - 1 WHERE device_id = %s AND creditos > 0',
        (device_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()
# =====================================================================
# PROCESAMIENTO EN SEGUNDO PLANO
# =====================================================================
# Render (y la mayoría de plataformas de hosting) cortan las peticiones HTTP
# que tardan demasiado — no es algo configurable desde nuestro lado. Por eso
# el vídeo NO se procesa dentro de la petición: se guarda, se lanza un hilo
# que lo procesa de verdad, y el navegador pregunta cada pocos segundos si
# ya está listo. Así la petición inicial responde al instante, sin importar
# cuánto tarde el vídeo en procesarse.
#
# NOTA: este diccionario vive en memoria del proceso. Funciona bien con un
# único worker (recomendado, ver Procfile), pero si en el futuro escalas a
# varios workers o instancias, cada uno tendría su propia copia y esto dejaría
# de funcionar correctamente — en ese caso habría que pasar esto a Redis o a
# una tabla de la base de datos.
TRABAJOS = {}

MENSAJE_ERROR_PROCESADO = "Something went wrong while processing your video. Please try a different video or trim it further."

def procesar_en_segundo_plano(job_id, video_path, pdf_path, parametros, device_id, es_pro, tenia_creditos_gratis):
    try:
        exito = procesar_video_partitura(video_path, pdf_path, **parametros)
    except Exception as e:
        print(f"Critical error during background processing: {e}")
        exito = False
    finally:
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
            except Exception:
                pass

    if exito:
        if not es_pro and tenia_creditos_gratis:
            descontar_credito_gratis(device_id)
        TRABAJOS[job_id] = {'estado': 'listo', 'pdf_path': pdf_path}
    else:
        TRABAJOS[job_id] = {'estado': 'error', 'mensaje': MENSAJE_ERROR_PROCESADO}


# =====================================================================
# CONFIGURACIÓN INDUSTRIAL DE STRIPE (VARIABLES DE ENTORNO SEGURAS)
# =====================================================================
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")

# =====================================================================
# RUTAS DE LA APLICACIÓN
# =====================================================================

@app.route('/', methods=['GET'])
def index():
    usuario_premium, es_pro, creditos_actuales, _ = _estado_usuario_actual()
    return render_template('index.html', usuario_premium=usuario_premium, es_pro=es_pro, creditos=creditos_actuales)

def _estado_usuario_actual():
    """Devuelve (usuario_premium, es_pro, creditos_actuales, user) para el visitante actual."""
    usuario_premium = False
    es_pro = False
    email_usuario = session.get('user_email')
    user = None

    # Los créditos gratuitos son por dispositivo, no por cuenta: da igual si el
    # visitante está registrado, ha iniciado sesión o no ha hecho nada de eso.
    creditos_actuales = obtener_creditos_gratis(g.device_id)

    if email_usuario:
        conn = obtener_conexion_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM usuarios WHERE email = %s', (email_usuario,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and user['is_pro'] == 1:
            es_pro = True
            usuario_premium = True
        elif creditos_actuales > 0:
            usuario_premium = True
    elif creditos_actuales > 0:
        usuario_premium = True

    return usuario_premium, es_pro, creditos_actuales, user

@app.route('/procesar', methods=['POST'])
def procesar():
    usuario_premium, es_pro, creditos_actuales, _ = _estado_usuario_actual()

    if 'video' not in request.files:
        return jsonify({'error': 'No video file was uploaded.'}), 400

    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No video file was selected.'}), 400

    formato = request.form.get('formato')
    es_horizontal = True if formato == '2' else False
    corte_superior = float(request.form.get('corte_sup', 0))
    corte_inferior = float(request.form.get('corte_inf', 0))
    inicio_seg = float(request.form.get('inicio_seg', 0))
    fin_seg_raw = request.form.get('fin_seg', '')
    fin_seg = float(fin_seg_raw) if fin_seg_raw else None
    titulo = request.form.get('titulo', '').strip()
    autor = request.form.get('autor', '').strip()

    # NUEVO: nombres únicos por trabajo — con el nombre fijo de antes, dos
    # personas subiendo un vídeo a la vez se pisaban los archivos entre sí.
    job_id = uuid.uuid4().hex
    _, extension = os.path.splitext(file.filename)
    video_path = os.path.join(UPLOAD_FOLDER, f"{job_id}{extension}")
    file.save(video_path)
    pdf_path = os.path.join(UPLOAD_FOLDER, f"{job_id}.pdf")

    parametros = dict(
        formato_horizontal=es_horizontal,
        corte_sup=corte_superior,
        corte_inf=corte_inferior,
        es_premium=usuario_premium,
        inicio_seg=inicio_seg,
        fin_seg=fin_seg,
        titulo=titulo,
        autor=autor,
    )

    TRABAJOS[job_id] = {'estado': 'procesando'}
    hilo = threading.Thread(
        target=procesar_en_segundo_plano,
        args=(job_id, video_path, pdf_path, parametros, g.device_id, es_pro, creditos_actuales > 0),
        daemon=True,
    )
    hilo.start()

    return jsonify({'job_id': job_id}), 202

@app.route('/estado/<job_id>')
def estado_trabajo(job_id):
    trabajo = TRABAJOS.get(job_id)
    if not trabajo:
        return jsonify({'estado': 'error', 'mensaje': 'This job could not be found (it may have expired).'}), 404
    return jsonify({'estado': trabajo['estado'], 'mensaje': trabajo.get('mensaje', '')})

@app.route('/descargar/<job_id>')
def descargar_trabajo(job_id):
    trabajo = TRABAJOS.get(job_id)
    if not trabajo or trabajo.get('estado') != 'listo':
        return "This file isn't ready yet.", 404
    pdf_path = trabajo['pdf_path']
    TRABAJOS.pop(job_id, None)
    return send_file(pdf_path, as_attachment=True, download_name='tu_partitura.pdf')

@app.route('/comprar/<tipo>')
def comprar(tipo):
    if 'user_email' not in session:
        return redirect(url_for('login'))
        
    try:
        if tipo == 'suscripcion':
            nombre_prod = "SheetMusic Pro Monthly Subscription"
            precio_centimos = 99 
            modo_pago = "subscription"
        else:
            return "Invalid plan"

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': nombre_prod},
                    'unit_amount': precio_centimos,
                    'recurring': {'interval': 'month'} if modo_pago == "subscription" else None
                },
                'quantity': 1,
            }],
            mode=modo_pago,
            success_url=url_for('pago_exitoso', tipo=tipo, _external=True),
            cancel_url=url_for('index', _external=True),
            customer_email=session['user_email']
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return f"Error connecting to Stripe: {e}"

@app.route('/pago-exitoso/<tipo>')
def pago_exitoso(tipo):
    if 'user_email' not in session:
        return redirect(url_for('login'))
        
    email = session['user_email']
    conn = obtener_conexion_db()
    cursor = conn.cursor()
    
    if tipo == 'suscripcion':
        cursor.execute('UPDATE usuarios SET is_pro = 1 WHERE email = %s', (email,))
        mensaje = "You've successfully subscribed to SheetMusic Pro! 🎉"
    else:
        mensaje = "Payment processed."
        
    conn.commit()
    cursor.close()
    conn.close()
    
    return f'''
        <div style="background:#0f172a;color:#f8fafc;height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;font-family:sans-serif;">
            <h2 style="color:#10b981;">Payment completed successfully!</h2>
            <p>{mensaje}</p>
            <a href="{url_for('index')}" style="background:#10b981;color:#0f172a;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;margin-top:20px;">Back to the Extractor</a>
        </div>
    '''

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or not password: 
            flash("Please fill in all fields.")
            return redirect(url_for('registro'))
        password_encriptada = generate_password_hash(password)
        conn = obtener_conexion_db()
        cursor = conn.cursor()
        try:
            # NUEVO: no se asignan créditos aquí — los créditos gratuitos son por
            # dispositivo (ver creditos_gratis), así que registrarse no da ni quita nada.
            cursor.execute('INSERT INTO usuarios (email, password) VALUES (%s, %s)', (email, password_encriptada))
            conn.commit()
            session.permanent = True  
            session['user_email'] = email
            return redirect(url_for('index'))
        except psycopg2.errors.UniqueViolation: 
            flash("This email is already registered.")
            return redirect(url_for('registro'))
        finally: 
            cursor.close()
            conn.close()
    return render_template('registro.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        conn = obtener_conexion_db()
        # CORRECCIÓN: Sintaxis correcta invocando el módulo extras importado
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM usuarios WHERE email = %s', (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session.permanent = True  
            session['user_email'] = user['email']
            return redirect(url_for('index'))
        else:
            flash("Incorrect email or password.")
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
