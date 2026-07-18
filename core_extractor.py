import cv2
import os
import numpy as np
import glob
from PIL import Image

def procesar_video_partitura(video_path, output_pdf_path, formato_horizontal=True):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    OUTPUT_DIR = os.path.join(BASE_DIR, "capturas_temporales")
    
    UMBRAL_MOVIMIENTO = 1.5   
    SALTAR_SEGUNDOS = 2       
    UMBRAL_DUPLICADOS = 0.95  
    PENTAGRAMAS_POR_FILA = 2  

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def limpiar_y_recortar_negro(frame_bgr):
        img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        arr = np.array(pil_img)
        filtro_blanco = (arr[:, :, 0] > 200) & (arr[:, :, 1] > 200) & (arr[:, :, 2] > 200)
        if not np.any(filtro_blanco):
            return pil_img
        coordenadas_y, coordenadas_x = np.argwhere(filtro_blanco).T
        return pil_img.crop((coordenadas_x.min(), coordenadas_y.min(), coordenadas_x.max(), coordenadas_y.max()))

    def son_imagenes_similes(img1, img2, umbral=0.95):
        i1 = img1.resize((100, 30)).convert("L")
        i2 = img2.resize((100, 30)).convert("L")
        a1, a2 = np.array(i1, dtype=np.float32), np.array(i2, dtype=np.float32)
        matriz_correlacion = np.corrcoef(a1.flatten(), a2.flatten())
        if matriz_correlacion.ndim > 1:
            valor_correlacion = matriz_correlacion[0, 1]
        else:
            valor_correlacion = 0.0
        return bool(valor_correlacion > umbral)

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or not cap.isOpened():
        return False
        
    skip_frames = int(fps * SALTAR_SEGUNDOS)
    prev_frame_gray = None
    count = 0
    raw_frames = []

    ret_inicial, frame_inicial = cap.read()
    if ret_inicial and frame_inicial is not None:
        raw_frames.append(frame_inicial.copy())
        prev_frame_gray = cv2.cvtColor(frame_inicial, cv2.COLOR_BGR2GRAY)
        prev_frame_gray = cv2.GaussianBlur(prev_frame_gray, (21, 21), 0)
        count += skip_frames
        cap.set(cv2.CAP_PROP_POS_FRAMES, count)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if prev_frame_gray is not None:
            frame_delta = cv2.absdiff(prev_frame_gray, gray)
            _, thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)
            cambio_porcentaje = (np.sum(thresh == 255) / thresh.size) * 100
            if cambio_porcentaje > UMBRAL_MOVIMIENTO:
                raw_frames.append(frame.copy())
        prev_frame_gray = gray
        count += skip_frames
        cap.set(cv2.CAP_PROP_POS_FRAMES, count)
    cap.release()

    if not raw_frames:
        return False

    if formato_horizontal:
        ANCHO_PAGINA, ALTO_PAGINA = 3508, 2480  
        MARGEN_LADO, MARGEN_TECHO = 100, 100
        ESPACIO_HORIZONTAL, ESPACIO_VERTICAL = 50, 50
        ancho_util = ANCHO_PAGINA - (MARGEN_LADO * 2)
        ancho_bloque = int((ancho_util - (ESPACIO_HORIZONTAL * (PENTAGRAMAS_POR_FILA - 1))) / PENTAGRAMAS_POR_FILA)
    else:
        ANCHO_PAGINA, ALTO_PAGINA = 2480, 3508  
        MARGEN_LADO, MARGEN_TECHO = 150, 150
        ESPACIO_VERTICAL = 50
        ancho_util = ANCHO_PAGINA - (MARGEN_LADO * 2)
        ancho_bloque = ancho_util

    fragmentos_unicos = []
    ultima_img_procesada = None

    for frame in raw_frames:
        img_limpia = limpiar_y_recortar_negro(frame)
        factor = ancho_bloque / img_limpia.width
        nuevo_alto = int(img_limpia.height * factor)
        img_resoli = img_limpia.resize((ancho_bloque, nuevo_alto), Image.Resampling.LANCZOS)
        
        if ultima_img_procesada is None or not son_imagenes_similes(img_resoli, ultima_img_procesada, UMBRAL_DUPLICADOS):
            fragmentos_unicos.append(img_resoli)
            ultima_img_procesada = img_resoli

    if not fragmentos_unicos:
        return False

    paginas_creadas = []
    def crear_nueva_pagina():
        return Image.new("RGB", (ANCHO_PAGINA, ALTO_PAGINA), (255, 255, 255))

    pagina_actual = crear_nueva_pagina()
    alto_maximo_util = ALTO_PAGINA - MARGEN_TECHO

    if formato_horizontal:
        columna = 0
        alto_maximo_fila = 0
        y_actual = MARGEN_TECHO

        for frag in fragmentos_unicos:
            if columna >= PENTAGRAMAS_POR_FILA:
                columna = 0
                y_actual += alto_maximo_fila + ESPACIO_VERTICAL
                alto_maximo_fila = 0
                
            if y_actual + frag.height > alto_maximo_util:
                paginas_creadas.append(pagina_actual)
                pagina_actual = crear_nueva_pagina()
                columna = 0
                y_actual = MARGEN_TECHO
                alto_maximo_fila = 0

            x_pos = MARGEN_LADO + (columna * (ancho_bloque + ESPACIO_HORIZONTAL))
            pagina_actual.paste(frag, (x_pos, y_actual))
            
            columna += 1
            if frag.height > alto_maximo_fila:
                alto_maximo_fila = frag.height
    else:
        y_actual = MARGEN_TECHO
        for frag in fragmentos_unicos:
            if y_actual + frag.height > alto_maximo_util:
                paginas_creadas.append(pagina_actual)
                pagina_actual = crear_nueva_pagina()
                y_actual = MARGEN_TECHO
            
            pagina_actual.paste(frag, (MARGEN_LADO, y_actual))
            y_actual += frag.height + ESPACIO_VERTICAL

    paginas_creadas.append(pagina_actual)

    if paginas_creadas:
        # CORRECCIÓN ABSOLUTA: Añadido el [0] para guardar desde la primera imagen PIL de la lista
        paginas_creadas[0].save(output_pdf_path, "PDF", save_all=True, append_images=paginas_creadas[1:])
        for f in glob.glob(os.path.join(OUTPUT_DIR, "*.png")):
            try: os.remove(f)
            except: pass
        try: os.rmdir(OUTPUT_DIR)
        except: pass
        return True
    return False
