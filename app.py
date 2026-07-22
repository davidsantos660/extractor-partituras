from flask import Flask, render_template, request, send_file, jsonify
import os
import uuid
from core_extractor import procesar_video_partitura

app = Flask(__name__)
app.secret_key = "clave_secreta_para_desarrollo"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def _procesar_solicitud():
    if 'video' not in request.files:
        return None, ('No video file was uploaded.', 400)

    file = request.files['video']
    if file.filename == '':
        return None, ('No video file was selected.', 400)

    formato = request.form.get('formato')
    es_horizontal = formato == '2'

    corte_superior = float(request.form.get('corte_sup', 0))
    corte_inferior = float(request.form.get('corte_inf', 0))
    inicio_seg = float(request.form.get('inicio_seg', 0))
    fin_seg_raw = request.form.get('fin_seg', '')
    fin_seg = float(fin_seg_raw) if fin_seg_raw else None
    titulo = request.form.get('titulo', '').strip()
    autor = request.form.get('autor', '').strip()

    job_id = uuid.uuid4().hex
    _, extension = os.path.splitext(file.filename)
    video_path = os.path.join(UPLOAD_FOLDER, f"{job_id}{extension}")
    pdf_path = os.path.join(UPLOAD_FOLDER, f"{job_id}.pdf")
    file.save(video_path)

    exito = procesar_video_partitura(
        video_path,
        pdf_path,
        formato_horizontal=es_horizontal,
        corte_sup=corte_superior,
        corte_inf=corte_inferior,
        inicio_seg=inicio_seg,
        fin_seg=fin_seg,
        titulo=titulo,
        autor=autor,
    )

    if os.path.exists(video_path):
        os.remove(video_path)

    if not exito:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        return None, ('Something went wrong while processing your video. Try trimming the video or using different crop settings.', 500)

    return pdf_path, None

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/procesar', methods=['POST'])
def procesar():
    pdf_path, error = _procesar_solicitud()
    if error:
        return jsonify({'error': error[0]}), error[1]
    return send_file(pdf_path, as_attachment=True, download_name='sheet_music.pdf')

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)
