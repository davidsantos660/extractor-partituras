import cv2
import os
import numpy as np
import glob
from PIL import Image, ImageDraw, ImageFont

def procesar_video_partitura(
    video_path,
    output_pdf_path,
    formato_horizontal=True,
    corte_sup=0,
    corte_inf=0,
    inicio_seg=0,
    fin_seg=None,
    titulo="",
    autor="",
):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    OUTPUT_DIR = os.path.join(BASE_DIR, "capturas_temporales")

    UMBRAL_MOVIMIENTO = 1.5
    SALTAR_SEGUNDOS = 2
    UMBRAL_DUPLICADOS = 0.95
    PENTAGRAMAS_POR_FILA = 2
    ALTO_TITULO = 130

    titulo = (titulo or "").strip()
    autor = (autor or "").strip()
    reservar_titulo = bool(titulo or autor)

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

    def recorte_es_claro(arr, umbral=185):
        return (arr[:, :, 0] > umbral) & (arr[:, :, 1] > umbral) & (arr[:, :, 2] > umbral)

    def quitar_bordes_oscuras(pil_img, umbral=185, min_ratio_fila=0.22):
        arr = np.array(pil_img.convert("RGB"))
        alto, ancho = arr.shape[:2]
        if alto == 0 or ancho == 0:
            return None

        filas_utiles = [
            y for y in range(alto)
            if np.mean(recorte_es_claro(arr[y:y + 1, :, :], umbral)) >= min_ratio_fila
        ]
        columnas_utiles = [
            x for x in range(ancho)
            if np.mean(recorte_es_claro(arr[:, x:x + 1, :], umbral)) >= min_ratio_fila
        ]

        if not filas_utiles or not columnas_utiles:
            return None

        y_min, y_max = filas_utiles[0], filas_utiles[-1]
        x_min, x_max = columnas_utiles[0], columnas_utiles[-1]

        if (y_max - y_min) < 30 or (x_max - x_min) < 100:
            return None

        return pil_img.crop((x_min, y_min, x_max + 1, y_max + 1))

    def es_fragmento_valido(pil_img, min_ratio_claro=0.62):
        arr = np.array(pil_img.convert("RGB"))
        ratio_claro = np.mean(recorte_es_claro(arr, 185))
        if ratio_claro < min_ratio_claro:
            return False
        if pil_img.width < 120 or pil_img.height < 20:
            return False
        return True

    def limpiar_y_recortar_negro(frame_bgr):
        img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        arr = np.array(pil_img)

        filtro_blanco = recorte_es_claro(arr, 200)
        if not np.any(filtro_blanco):
            return None

        coordenadas_y, coordenadas_x = np.argwhere(filtro_blanco).T
        if len(coordenadas_x) < 100 or len(coordenadas_y) < 20:
            return None

        x_min, x_max = coordenadas_x.min(), coordenadas_x.max()
        y_min, y_max = coordenadas_y.min(), coordenadas_y.max()

        if (y_max - y_min) < 30 or (x_max - x_min) < 100:
            return None

        recorte = pil_img.crop((x_min, y_min, x_max, y_max))
        recorte = quitar_bordes_oscuras(recorte)
        if recorte is None or not es_fragmento_valido(recorte):
            return None

        return recorte

    def son_imagenes_similes(img1, img2, umbral=0.95):
        i1 = img1.resize((100, 30)).convert("L")
        i2 = img2.resize((100, 30)).convert("L")
        a1, a2 = np.array(i1, dtype=np.float32), np.array(i2, dtype=np.float32)
        matriz_correlacion = np.corrcoef(a1.flatten(), a2.flatten())

        if matriz_correlacion.ndim > 1:
            valor_correlacion = float(matriz_correlacion[0, 1])
        else:
            valor_correlacion = 0.0
        return bool(valor_correlacion > umbral)

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or not cap.isOpened():
        return False

    skip_frames = int(fps * SALTAR_SEGUNDOS)
    prev_frame_gray = None
    raw_frames = []

    ret_init, frame_init = cap.read()
    if not ret_init or frame_init is None:
        return False
    alto, ancho = frame_init.shape[:2]

    y1 = int(alto * (corte_sup / 100))
    y2 = int(alto * ((100 - corte_inf) / 100))
    if y1 >= y2:
        y1, y2 = 0, alto

    segundo_inicio = max(inicio_seg, 0)
    count = int(fps * segundo_inicio)
    cap.set(cv2.CAP_PROP_POS_FRAMES, count)
    frame_limite = int(fps * fin_seg) if fin_seg else None

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        if frame_limite is not None and count >= frame_limite:
            break

        frame_recortado = frame[y1:y2, 0:ancho]
        gray = cv2.cvtColor(frame_recortado, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if prev_frame_gray is None:
            raw_frames.append(frame_recortado.copy())
        else:
            frame_delta = cv2.absdiff(prev_frame_gray, gray)
            _, thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)
            cambio_porcentaje = (np.sum(thresh == 255) / thresh.size) * 100
            if cambio_porcentaje > UMBRAL_MOVIMIENTO:
                raw_frames.append(frame_recortado.copy())

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
        if img_limpia is None:
            continue

        factor = ancho_bloque / img_limpia.width
        nuevo_alto = int(img_limpia.height * factor)
        img_resoli = img_limpia.resize((ancho_bloque, nuevo_alto), Image.Resampling.LANCZOS)

        if ultima_img_procesada is None or not son_imagenes_similes(img_resoli, ultima_img_procesada, UMBRAL_DUPLICADOS):
            fragmentos_unicos.append(img_resoli)
            ultima_img_procesada = img_resoli

    if not fragmentos_unicos:
        return False

    def ratio_area_clara(img):
        arr = np.array(img.convert("RGB"))
        return float(np.mean(recorte_es_claro(arr, 185)))

    if formato_horizontal and len(fragmentos_unicos) >= 2:
        ratio_primero = ratio_area_clara(fragmentos_unicos[0])
        ratio_segundo = ratio_area_clara(fragmentos_unicos[1])
        if ratio_primero + 0.12 < ratio_segundo:
            fragmentos_unicos = fragmentos_unicos[1:]

    if not fragmentos_unicos:
        return False

    paginas_creadas = []

    def crear_nueva_pagina():
        return Image.new("RGB", (ANCHO_PAGINA, ALTO_PAGINA), (255, 255, 255))

    pagina_actual = crear_nueva_pagina()
    alto_maximo_util = ALTO_PAGINA - MARGEN_TECHO
    margen_techo_primera = MARGEN_TECHO + (ALTO_TITULO if reservar_titulo else 0)

    if formato_horizontal:
        columna = 0
        alto_maximo_fila = 0
        y_actual = margen_techo_primera

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
        y_actual = margen_techo_primera
        for frag in fragmentos_unicos:
            if y_actual + frag.height > alto_maximo_util:
                paginas_creadas.append(pagina_actual)
                pagina_actual = crear_nueva_pagina()
                y_actual = MARGEN_TECHO

            pagina_actual.paste(frag, (MARGEN_LADO, y_actual))
            y_actual += frag.height + ESPACIO_VERTICAL

    paginas_creadas.append(pagina_actual)

    if reservar_titulo and paginas_creadas:
        draw_titulo = ImageDraw.Draw(paginas_creadas[0])
        if titulo:
            fuente_titulo = cargar_fuente(RUTA_FUENTE_TITULO, 46, 44)
            draw_titulo.text(
                (ANCHO_PAGINA // 2, MARGEN_TECHO + 40),
                titulo,
                fill=(15, 23, 42),
                font=fuente_titulo,
                anchor="mm",
            )
        if autor:
            fuente_autor = cargar_fuente(RUTA_FUENTE_AUTOR, 26, 24)
            draw_titulo.text(
                (ANCHO_PAGINA - MARGEN_LADO, MARGEN_TECHO + 85),
                autor,
                fill=(71, 85, 105),
                font=fuente_autor,
                anchor="rm",
            )

    if paginas_creadas and len(paginas_creadas) > 0:
        try:
            primera_pagina = paginas_creadas[0]
            resto_paginas = [img for img in paginas_creadas[1:] if isinstance(img, Image.Image)]

            primera_pagina.save(
                output_pdf_path,
                "PDF",
                save_all=True,
                append_images=resto_paginas,
            )

            for f in glob.glob(os.path.join(OUTPUT_DIR, "*.png")):
                try:
                    os.remove(f)
                except Exception:
                    pass
            try:
                os.rmdir(OUTPUT_DIR)
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"Error crítico al guardar el PDF: {e}")
            return False

    return False
