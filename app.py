from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash
import os
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
            creditos INT DEFAULT 0
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

inicializar_base_de_datos()

# =====================================================================
# CONFIGURACIÓN INDUSTRIAL DE STRIPE (VARIABLES DE ENTORNO SEGURAS)
# =====================================================================
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")

# =====================================================================
# RUTAS DE LA APLICACIÓN
# =====================================================================

@app.route('/', methods=['GET', 'POST'])
def index():
    usuario_premium = False
    es_pro = False
    email_usuario = session.get('user_email')
    creditos_actuales = 0
    user = None
    
    if email_usuario:
        conn = obtener_conexion_db()
        # CORRECCIÓN: Sintaxis correcta invocando el módulo extras importado
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM usuarios WHERE email = %s', (email_usuario,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if user:
            creditos_actuales = user['creditos']
            if user['is_pro'] == 1:
                es_pro = True
                usuario_premium = True
            elif user['creditos'] > 0:
                usuario_premium = True

    if request.method == 'POST':
        if 'video' not in request.files:
            return redirect(request.url)
        
        file = request.files['video']
        if file.filename == '':
            return redirect(request.url)
        
        formato = request.form.get('formato')
        es_horizontal = True if formato == '2' else False
        corte_superior = float(request.form.get('corte_sup', 0))
        corte_inferior = float(request.form.get('corte_inf', 0))

        # NUEVO: rango de duración elegido por el usuario (evita intro/outro)
        inicio_seg = float(request.form.get('inicio_seg', 0))
        fin_seg_raw = request.form.get('fin_seg', '')
        fin_seg = float(fin_seg_raw) if fin_seg_raw else None

        # NUEVO: título personalizado opcional para la primera página
        titulo = request.form.get('titulo', '').strip()
        
        if file:
            video_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(video_path)
            
            pdf_filename = "tu_partitura.pdf"
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
            
            exito = procesar_video_partitura(
                video_path, 
                pdf_path, 
                formato_horizontal=es_horizontal,
                corte_sup=corte_superior,
                corte_inf=corte_inferior,
                es_premium=usuario_premium,
                inicio_seg=inicio_seg,
                fin_seg=fin_seg,
                titulo=titulo
            )
            
            if os.path.exists(video_path):
                os.remove(video_path)
                
            if exito:
                if email_usuario and user and not user['is_pro'] and user['creditos'] > 0:
                    conn = obtener_conexion_db()
                    cursor = conn.cursor()
                    cursor.execute('UPDATE usuarios SET creditos = creditos - 1 WHERE email = %s', (email_usuario,))
                    conn.commit()
                    cursor.close()
                    conn.close()
                return send_file(pdf_path, as_attachment=True)
            else:
                return "Error al procesar el vídeo musical."
                
    return render_template('index.html', usuario_premium=usuario_premium, es_pro=es_pro, creditos=creditos_actuales)

@app.route('/comprar/<tipo>')
def comprar(tipo):
    if 'user_email' not in session:
        return redirect(url_for('login'))
        
    try:
        if tipo == 'credito':
            nombre_prod = "1 Crédito de Partitura Completa"
            precio_centimos = 95 
            modo_pago = "payment"
        elif tipo == 'suscripcion':
            nombre_prod = "Suscripción Mensual SheetMusic Pro"
            precio_centimos = 299 
            modo_pago = "subscription"
        else:
            return "Plan no válido"

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
        return f"Error al conectar con la pasarela de Stripe: {e}"

@app.route('/pago-exitoso/<tipo>')
def pago_exitoso(tipo):
    if 'user_email' not in session:
        return redirect(url_for('login'))
        
    email = session['user_email']
    conn = obtener_conexion_db()
    cursor = conn.cursor()
    
    if tipo == 'credito':
        cursor.execute('UPDATE usuarios SET creditos = creditos + 1 WHERE email = %s', (email,))
        mensaje = "Has añadido 1 crédito de descarga suelta con éxito. 🎉"
    elif tipo == 'suscripcion':
        cursor.execute('UPDATE usuarios SET is_pro = 1 WHERE email = %s', (email,))
        mensaje = "¡Te has suscrito con éxito a SheetMusic Pro! 🎉"
        
    conn.commit()
    cursor.close()
    conn.close()
    
    return f'''
        <div style="background:#0f172a;color:#f8fafc;height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;font-family:sans-serif;">
            <h2 style="color:#10b981;">¡Pago completado con éxito!</h2>
            <p>{mensaje}</p>
            <a href="{url_for('index')}" style="background:#10b981;color:#0f172a;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;margin-top:20px;">Volver al Extractor</a>
        </div>
    '''

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or not password: 
            flash("Campos obligatorios vacíos.")
            return redirect(url_for('registro'))
        password_encriptada = generate_password_hash(password)
        conn = obtener_conexion_db()
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO usuarios (email, password) VALUES (%s, %s)', (email, password_encriptada))
            conn.commit()
            session.permanent = True  
            session['user_email'] = email
            return redirect(url_for('index'))
        except psycopg2.errors.UniqueViolation: 
            flash("Este correo electrónico ya está registrado.")
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
            flash("Correo electrónico o contraseña incorrectos.")
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
