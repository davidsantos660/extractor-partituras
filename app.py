from flask import Flask, render_template, request, send_file, flash, redirect
import os
from core_extractor import procesar_video_partitura

app = Flask(__name__)
app.secret_key = "clave_secreta_para_desarrollo"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'video' not in request.files:
            return redirect(request.url)
        
        file = request.files['video']
        if file.filename == '':
            return redirect(request.url)
        
        formato = request.form.get('formato')
        es_horizontal = True if formato == '2' else False
        
        if file:
            video_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(video_path)
            
            pdf_filename = "tu_partitura.pdf"
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
            
            exito = procesar_video_partitura(video_path, pdf_path, formato_horizontal=es_horizontal)
            
            if os.path.exists(video_path):
                os.remove(video_path)
                
            if exito:
                return send_file(pdf_path, as_attachment=True)
            else:
                return "Error al procesar el vídeo musical."
                
    return render_template('index.html')

if __name__ == '__main__':
    # FORZADO DE PUERTO DINÁMICO PARA INTERNET
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)
