import cv2
import os
import glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def procesar_video_partitura(video_path, output_pdf_path, formato_horizontal=True, corte_sup=0, corte_inf=0, es_premium=False, inicio_seg=0, fin_seg=None, titulo="", autor=""):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    OUTPUT_DIR = os.path.join(BASE_DIR, "capturas_temporales")
    UMBRAL_MOVIMIENTO = 2.0
    SALTAR_SEGUNDOS = 3  # Aumentamos el salto para procesar el video un 50% más rápido y evitar Timeouts
    UMBRAL_DUPLICADOS = 95.0  # Porcentaje de similitud
    PENTAGRAMAS_POR_FILA = 2
    ALTO_TITULO = 130  # Space reserved at the top of page 1 for the title/author/brand
    titulo = (titulo or "").strip()
    autor = (autor or "").strip()

    # NOTE: placeholder brand name — change this later once you pick a final name for the site
    BRAND_NAME = "Music Sheet Xtractor"

    # NUEVO: fuentes personalizadas (limpias) con fallback seguro a la fuente por defecto de Pillow
    RUTA_FUENTE_TITULO = os.path.join(BASE_DIR, "static", "fonts", "Poppins-SemiBold.ttf")
    RUTA_FUENTE_AUTOR = os.path.join(BASE_DIR, "static", "fonts", "Poppins-Regular.ttf")

    def cargar_fuente(ruta, tamano, tamano_fallback):
        try:
            return ImageFont.truetype(ruta, tamano)
        except Exception:
            try:
                return ImageFont.load_default(size=tamano_fallback)
            except TypeError:
                return ImageFont.load_default()
    
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def limpiar_y_recortar_negro(frame_bgr):
        # Mantenemos tu algoritmo original pero protegido contra arrays vacíos
        img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        arr = np.array(pil_img)
        filtro_blanco = (arr[:, :, 0] > 200) & (arr[:, :, 1] > 200) & (arr[:, :, 2] > 200)
        if not np.any(filtro_blanco):
            return None
        coordenadas_y, coordenadas_x = np.argwhere(filtro_blanco).T
        if len(coordenadas_x) < 100 or len(coordenadas_y) < 20:
            return None
        x_min, x_max = coordenadas_x.min(), coordenadas_x.max()
        y_min, y_max = coordenadas_y.min(), coordenadas_y.max()
        if (y_max - y_min) < 30 or (x_max - x_min) < 100:
            return None
        return pil_img.crop((x_min, y_min, x_max, y_max))

    def son_imagenes_similes_cv2(frame1, frame2, umbral=95.0):
        # Solución definitiva: Comparación ultra veloz por diferencia de píxeles nativa en OpenCV
        # Evita crashes matemáticos por división por cero o matrices vacías
        if frame1 is None or frame2 is None:
            return False
        g1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
        g1 = cv2.resize(g1, (100, 40))
        g2 = cv2.resize(g2, (100, 40))
        diferencia = cv2.absdiff(g1, g2)
        _, t = cv2.threshold(diferencia, 25, 255, cv2.THRESH_BINARY)
        similitud = 100.0 - ((np.sum(t == 255) / t.size) * 100.0)
        return similitud > umbral

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or not cap.isOpened():
        return False
    skip_frames = int(fps * SALTAR_SEGUNDOS)
    prev_frame_gray = None
    
    if formato_horizontal:
        ANCHO_PAGINA, ALTO_PAGINA = 1920, 1357  
        MARGEN_LADO, MARGEN_TECHO, ESPACIO_HORIZONTAL, ESPACIO_VERTICAL = 50, 50, 25, 25
        ancho_util = ANCHO_PAGINA - (MARGEN_LADO * 2)
        ancho_bloque = int((ancho_util - (ESPACIO_HORIZONTAL * (PENTAGRAMAS_POR_FILA - 1))) / PENTAGRAMAS_POR_FILA)
    else:
        ANCHO_PAGINA, ALTO_PAGINA = 1357, 1920  
        MARGEN_LADO, MARGEN_TECHO, ESPACIO_VERTICAL = 70, 35, 10
        ancho_bloque = ANCHO_PAGINA - (MARGEN_LADO * 2)

    fragmentos_unicos = []
    ultimo_frame_procesado = None

    ret_init, frame_init = cap.read()
    if not ret_init or frame_init is None:
        return False
    alto, ancho = frame_init.shape[:2]
    
    y1 = int(alto * (corte_sup / 100))
    y2 = int(alto * ((100 - corte_inf) / 100))
    if y1 >= y2 or y1 >= alto or y2 <= 0:
        y1, y2 = 0, alto

    # ---------------------------------------------------------------
    # NUEVO: recorte de duración (evita procesar intro/outro sin partitura)
    # ---------------------------------------------------------------
    segundo_inicio = max(inicio_seg, 0)
    count = int(fps * segundo_inicio)
    cap.set(cv2.CAP_PROP_POS_FRAMES, count)

    frame_limite = int(fps * fin_seg) if fin_seg else None

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        # NUEVO: detener el procesamiento al llegar al segundo final elegido
        if frame_limite is not None and count >= frame_limite:
            break

        frame_recortado = frame[y1:y2, 0:ancho]
        gray = cv2.cvtColor(frame_recortado, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        
        detectar_cambio = False
        if prev_frame_gray is None:
            detectar_cambio = True
        else:
            frame_delta = cv2.absdiff(prev_frame_gray, gray)
            _, thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)
            cambio_porcentaje = (np.sum(thresh == 255) / thresh.size) * 100
            if cambio_porcentaje > UMBRAL_MOVIMIENTO:
                detectar_cambio = True
                
        prev_frame_gray = gray

        if detectar_cambio:
            # Comparamos duplicados usando OpenCV nativo antes de convertir a Pillow (Ahorra un 400% de CPU)
            if ultimo_frame_procesado is None or not son_imagenes_similes_cv2(frame_recortado, ultimo_frame_procesado, UMBRAL_DUPLICADOS):
                img_limpia = limpiar_y_recortar_negro(frame_recortado)
                if img_limpia is not None:
                    factor = ancho_bloque / img_limpia.width
                    img_resoli = img_limpia.resize((ancho_bloque, int(img_limpia.height * factor)), Image.Resampling.LANCZOS)
                    momento_seg = count / fps  # NUEVO: segundo exacto del video en que se capturó este fragmento
                    fragmentos_unicos.append((img_resoli, momento_seg))
                    ultimo_frame_procesado = frame_recortado.copy()
                    
        count += skip_frames
        cap.set(cv2.CAP_PROP_POS_FRAMES, count)
        
    cap.release()
    
    if not fragmentos_unicos:
        return False

    paginas_creadas = []
    paginas_con_borroso = set()
    pagina_actual_indice = 0

    def crear_nueva_pagina():
        return Image.new("RGB", (ANCHO_PAGINA, ALTO_PAGINA), (255, 255, 255))
    
    pagina_actual = crear_nueva_pagina()
    alto_maximo_util = ALTO_PAGINA - MARGEN_TECHO

    # NOTE: the top space is now always reserved on page 1, since the brand name always shows there
    margen_techo_primera = MARGEN_TECHO + ALTO_TITULO

    # Free users see the content blurred past this point in the video (heavy blur — visible but unusable)
    SEGUNDOS_GRATIS = 30
    umbral_borroso_seg = segundo_inicio + SEGUNDOS_GRATIS
    RADIO_DESENFOQUE = 24

    contador_fragmentos_gratis = 0
    marcas_de_agua = {}  # page index -> list of (x_center, y_center) where the watermark text goes

    def preparar_fragmento(frag_img, momento_seg):
        nonlocal contador_fragmentos_gratis
        if es_premium:
            return frag_img, False, False

        contador_fragmentos_gratis += 1
        marcar_agua = (contador_fragmentos_gratis % 2 == 0)  # every other fragment, so it's not too intrusive

        if momento_seg > umbral_borroso_seg:
            return frag_img.filter(ImageFilter.GaussianBlur(radius=RADIO_DESENFOQUE)), True, marcar_agua
        return frag_img, False, marcar_agua

    if formato_horizontal:
        columna = 0
        alto_maximo_fila = 0
        y_actual = margen_techo_primera
        for frag_img, momento_seg in fragmentos_unicos:
            frag, es_borroso, marcar_agua = preparar_fragmento(frag_img, momento_seg)
            if columna >= PENTAGRAMAS_POR_FILA:
                columna = 0
                y_actual += alto_maximo_fila + ESPACIO_VERTICAL
                alto_maximo_fila = 0
            if y_actual + frag.height > alto_maximo_util:
                paginas_creadas.append(pagina_actual)
                pagina_actual_indice += 1
                pagina_actual = crear_nueva_pagina()
                columna = 0
                y_actual = MARGEN_TECHO
                alto_maximo_fila = 0
            x_pos = MARGEN_LADO + (columna * (ancho_bloque + ESPACIO_HORIZONTAL))
            pagina_actual.paste(frag, (x_pos, y_actual))
            if es_borroso:
                paginas_con_borroso.add(pagina_actual_indice)
            if marcar_agua:
                marcas_de_agua.setdefault(pagina_actual_indice, []).append((x_pos + frag.width / 2, y_actual + frag.height / 2))
            columna += 1
            if frag.height > alto_maximo_fila:
                alto_maximo_fila = frag.height
    else:
        y_actual = margen_techo_primera
        for frag_img, momento_seg in fragmentos_unicos:
            frag, es_borroso, marcar_agua = preparar_fragmento(frag_img, momento_seg)
            if y_actual + frag.height > alto_maximo_util:
                paginas_creadas.append(pagina_actual)
                pagina_actual_indice += 1
                pagina_actual = crear_nueva_pagina()
                y_actual = MARGEN_TECHO
            pagina_actual.paste(frag, (MARGEN_LADO, y_actual))
            if es_borroso:
                paginas_con_borroso.add(pagina_actual_indice)
            if marcar_agua:
                marcas_de_agua.setdefault(pagina_actual_indice, []).append((MARGEN_LADO + frag.width / 2, y_actual + frag.height / 2))
            y_actual += frag.height + ESPACIO_VERTICAL
            
    paginas_creadas.append(pagina_actual)

    # Title, author and brand name on page 1.
    # - With a title: title centered, author top-right (if given), brand name in small text
    #   right below wherever the author line is (or in its place, if there's no author).
    # - Without a title: the brand name becomes a small subtitle in the title's place instead,
    #   so the page doesn't end up with a floating brand line and no title above it.
    if paginas_creadas:
        draw_titulo = ImageDraw.Draw(paginas_creadas[0])
        fuente_marca = cargar_fuente(RUTA_FUENTE_AUTOR, 20, 18)

        if titulo:
            fuente_titulo = cargar_fuente(RUTA_FUENTE_TITULO, 46, 44)
            draw_titulo.text((ANCHO_PAGINA // 2, MARGEN_TECHO + 40), titulo, fill=(15, 23, 42), font=fuente_titulo, anchor="mm")

            if autor:
                fuente_autor = cargar_fuente(RUTA_FUENTE_AUTOR, 26, 24)
                draw_titulo.text((ANCHO_PAGINA - MARGEN_LADO, MARGEN_TECHO + 85), autor, fill=(71, 85, 105), font=fuente_autor, anchor="rm")
                draw_titulo.text((ANCHO_PAGINA - MARGEN_LADO, MARGEN_TECHO + 112), BRAND_NAME, fill=(148, 163, 184), font=fuente_marca, anchor="rm")
            else:
                draw_titulo.text((ANCHO_PAGINA - MARGEN_LADO, MARGEN_TECHO + 85), BRAND_NAME, fill=(148, 163, 184), font=fuente_marca, anchor="rm")
        else:
            draw_titulo.text((ANCHO_PAGINA // 2, MARGEN_TECHO + 40), BRAND_NAME, fill=(148, 163, 184), font=fuente_marca, anchor="mm")

            if autor:
                fuente_autor = cargar_fuente(RUTA_FUENTE_AUTOR, 26, 24)
                draw_titulo.text((ANCHO_PAGINA - MARGEN_LADO, MARGEN_TECHO + 85), autor, fill=(71, 85, 105), font=fuente_autor, anchor="rm")

    # Subscribe message — bigger and bolder, on every page that actually has blurred content
    if not es_premium and paginas_con_borroso:
        fuente_footer = cargar_fuente(RUTA_FUENTE_TITULO, 34, 30)
        for idx in sorted(paginas_con_borroso):
            draw = ImageDraw.Draw(paginas_creadas[idx])
            msg = "🔒 Subscribe to Pro to unlock the full, unblurred sheet music"
            caja = draw.textbbox((ANCHO_PAGINA // 2, ALTO_PAGINA - 55), msg, font=fuente_footer, anchor="mm")
            padding = 14
            draw.rectangle(
                (caja[0] - padding, caja[1] - padding, caja[2] + padding, caja[3] + padding),
                fill=(15, 23, 42)
            )
            draw.text((ANCHO_PAGINA // 2, ALTO_PAGINA - 55), msg, fill=(255, 255, 255), font=fuente_footer, anchor="mm")

    # Translucent brand watermark on free-tier pages, over every other fragment
    if not es_premium and marcas_de_agua:
        fuente_marca_agua = cargar_fuente(RUTA_FUENTE_AUTOR, 24, 22)
        for idx, posiciones in marcas_de_agua.items():
            capa = Image.new("RGBA", paginas_creadas[idx].size, (0, 0, 0, 0))
            draw_capa = ImageDraw.Draw(capa)
            for (cx, cy) in posiciones:
                draw_capa.text((cx, cy), BRAND_NAME, fill=(100, 116, 139, 90), font=fuente_marca_agua, anchor="mm")
            paginas_creadas[idx] = Image.alpha_composite(paginas_creadas[idx].convert("RGBA"), capa).convert("RGB")

    if paginas_creadas and len(paginas_creadas) > 0:
        try:
            primera_pagina = paginas_creadas[0]
            resto_paginas = [img for img in paginas_creadas[1:] if isinstance(img, Image.Image)]
            primera_pagina.save(output_pdf_path, "PDF", save_all=True, append_images=resto_paginas)
            
            for f in glob.glob(os.path.join(OUTPUT_DIR, "*.png")):
                try: os.remove(f)
                except: pass
            try: os.rmdir(OUTPUT_DIR)
            except: pass
                
            return True
        except Exception as e:
            print(f"Critical error while saving the PDF: {e}")
            return False
            
    return False
