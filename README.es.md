<h1 align="center">FastCollageForWin</h1>

<p align="center">
  Editor de collages de escritorio para Windows: lienzo libre, rejillas
  generadas y ajuste automático del lienzo a las fotos.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/GUI-PySide6%20%7C%20Qt%206-41CD52?logo=qt&logoColor=white" alt="PySide6, Qt 6">
  <img src="https://img.shields.io/badge/build-PyInstaller-FFD43B?logo=python&logoColor=black" alt="PyInstaller">
  <img src="https://img.shields.io/badge/i18n-EN%20%7C%20RU%20%7C%20ES%20%7C%20ZH%20%7C%20AR-2E86C1" alt="Idiomas de la interfaz">
  <img src="https://img.shields.io/badge/version-2.1.0-757575" alt="Versión 2.1.0">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.ru.md">Русский</a> ·
  <b>Español</b> ·
  <a href="README.zh.md">中文</a> ·
  <a href="README.ar.md">العربية</a>
</p>

<p align="center">
  <img src="https://github.com/user-attachments/assets/2d1062fd-11d9-440e-8e38-3b4127bf624f" alt="FastCollageForWin">
</p>

---

## Contenido

- [Qué es](#about)
- [Características](#features)
- [Requisitos](#requirements)
- [Instalación y uso](#install)
- [Compilar un ejecutable](#build)
- [Primeros pasos](#quickstart)
- [Ajustar el lienzo a las fotos](#autofit)
- [Teclado y ratón](#keys)
- [Exportar](#export)
- [Proyectos, ajustes y registro](#projects)
- [Estructura del repositorio](#layout)
- [Límites](#limits)
- [Licencia](LICENSE)

<a id="about"></a>

## Qué es

FastCollageForWin convierte un montón de fotos en una sola imagen. Añades las
fotos al panel de la izquierda, las colocas en el lienzo a mano o en una rejilla
generada, ajustas cada una (zoom, desplazamiento, rotación, espejo) y exportas
el resultado como un único PNG o JPEG.

Es una aplicación de escritorio escrita en Python con PySide6 (Qt 6) y
empaquetada como un único ejecutable de Windows sin consola mediante
PyInstaller.

Dos formas de trabajar:

- **Collage libre**: cada foto es un objeto independiente que mueves, escalas y
  giras por todo el lienzo.
- **Collage aleatorio**: la aplicación genera una rejilla de huecos; la foto que
  sueltas en un hueco lo rellena por completo y los bordes entre huecos se
  pueden arrastrar para repartir el espacio.

**Para quién es:**

- para quien necesita un collage limpio y rápido, sin abrir un editor completo;
- para diseñadores y fotógrafos que preparan maquetas y previsualizaciones;
- para quien hace fotolibros, pósteres, miniaturas o publicaciones en redes.

<a id="features"></a>

## Características

- Dos modos de collage: colocación libre y rejillas generadas con bordes de
  hueco arrastrables.
- **Ajustar el lienzo a las fotos** (`Ctrl+Shift+A`): la aplicación calcula el
  tamaño del lienzo y la composición a partir de las fotos elegidas, en dos
  modos: proporciones exactas, o un presupuesto de recorte con una lista de
  composiciones alternativas.
- Edición por foto: zoom, desplazamiento, rotación libre, espejo horizontal y
  vertical, orden de capas.
- Estilo del collage: separación entre fotos, radio de las esquinas, color de
  fondo o fondo totalmente transparente.
- 25 presets de lienzo agrupados en pantalla, redes sociales e impresión (A5-A3
  y formatos fotográficos a 300 ppp), más cualquier tamaño propio de 100 a
  10000 px.
- Exportación a PNG o JPEG con escala del 10 al 400 %, control de calidad JPEG,
  estimación del tamaño del archivo antes de guardar y renderizado por bandas
  que mantiene el consumo de memoria estable en lienzos enormes.
- Los proyectos (`.fcproj`, JSON por dentro) guardan **rutas relativas** a las
  imágenes: si mueves la carpeta, el collage sigue abriéndose, y los archivos
  que falten se pueden volver a vincular.
- Deshacer y rehacer, 100 pasos por defecto.
- Las imágenes se cargan en un grupo de hilos en segundo plano, con progreso
  cancelable.
- Panel de imágenes con arrastrar y soltar desde el Explorador, miniaturas y un
  atajo que devuelve una foto del lienzo al panel.
- Portapapeles: copiar, pegar y duplicar objetos.
- Cinco idiomas de interfaz (inglés, ruso, español, chino y árabe) con
  disposición de derecha a izquierda para el árabe.
- Los atajos funcionan con cualquier distribución de teclado: las teclas de modo
  se leen por el código físico de la tecla.
- Registro con rotación: ningún fallo silencioso se queda sin rastro.
- En una cuadrícula cada foto se recorta a su celda: las fotos nunca se solapan
  y la cuadrícula ocupa todo el lienzo; los únicos huecos son los márgenes que
  definas.
- El collage libre no limita el número de fotos: el único límite es la memoria.
- El panel de imágenes es acoplable: **Ver → Panel de imágenes** lo oculta o lo
  muestra, se puede desacoplar y vaciar con un clic, y su estado se restaura en
  el siguiente arranque. **Archivo → Cargar en el panel** lo llena sin colocar
  nada en el lienzo.

<a id="requirements"></a>

## Requisitos

- **Windows 10 u 11.** La aplicación está pensada para Windows: la ruta del
  registro y los atajos independientes de la distribución asumen ese sistema.
  El resto es Python y Qt sin más.
- **Python 3.9 o superior**
- **PySide6** (Qt 6): la única dependencia.

<a id="install"></a>

## Instalación y uso

```powershell
git clone https://github.com/re-quies/fastcollageforwin
cd fastcollageforwin
python -m venv .venv
.venv\Scripts\activate
pip install PySide6
python main.py
```

<a id="build"></a>

## Compilar un ejecutable

```powershell
pip install pyinstaller
pyinstaller main.spec
```

El archivo spec genera un único ejecutable sin consola (`console=False`) e
incluye la carpeta `assets/`, para que los iconos sobrevivan al empaquetado. El
resultado es `dist/main.exe`; puedes renombrarlo.

La versión del título de la ventana viene de `core/version.py`. Si tras compilar
sigue apareciendo la versión anterior, PyInstaller tomó archivos obsoletos: el
problema está en la compilación, no en el código.

<a id="quickstart"></a>

## Primeros pasos

1. Abre la aplicación. El diálogo **Nuevo collage** pregunta el modo (libre o
   aleatorio), el número de fotos para la rejilla y el tamaño del lienzo: un
   preset o una anchura y altura propias.
2. Añade fotos: **Archivo → Añadir imagen** (`Ctrl+O`), o arrastra archivos
   desde el Explorador al panel de imágenes.
3. Arrastra una miniatura del panel al lienzo o a un hueco de la rejilla.
4. Ajusta la foto: mantén `Z` y gira la rueda para el zoom del contenido,
   mantén `C` y arrastra para moverla dentro del hueco, `R` y arrastra para
   girarla. `X` devuelve la foto seleccionada al panel.
5. Estilo en **Lienzo → Espaciado y fondo...**: separación entre fotos, radio
   de las esquinas, color de fondo o transparencia.
6. Exporta con **Archivo → Exportar** (`Ctrl+E`): PNG o JPEG, escala y
   calidad.

<a id="autofit"></a>

## Ajustar el lienzo a las fotos

**Lienzo → Lienzo según las fotos** (`Ctrl+Shift+A`) lee las fotos del panel y
propone un tamaño de lienzo junto con una composición, en lugar de obligarte a
adivinar el tamaño primero. El redondeo es configurable: exacto, a 10 px, a
100 px o un número redondo automático.

Dos modos de ajuste:

- **Proporciones exactas (sin recorte)**: cada foto conserva sus proporciones y
  no se corta nada. Se propone una única composición, la mejor.
- **Más opciones (recorte hasta N %)**: cada foto puede perder hasta un N % de
  un lado, así que sus proporciones se acercan a un objetivo cómodo. Eso abre
  decenas de rejillas distintas, ordenadas de mejor a peor; el botón **Otra
  cuadrícula** las recorre y el contador muestra «Opción 3 de 12».

El presupuesto de recorte y el número de composiciones se configuran en
**Ajustes → Ajuste del lienzo** (0-50 % y 2-48; por defecto 20 % y 12). Solo
afectan al segundo modo; el primero los ignora.

Más recorte no es automáticamente mejor: a partir de un 35 % aproximadamente,
fotos distintas se acercan a las mismas proporciones, las composiciones empiezan
a repetirse y el número de rejillas únicas vuelve a bajar. El punto óptimo está
entre el 20 y el 35 %.

<a id="keys"></a>

## Teclado y ratón

### Archivos

| Teclas | Acción |
| --- | --- |
| `Ctrl+O` | Añadir imagen |
| `Ctrl+Shift+O` | Abrir proyecto |
| `Ctrl+S` / `Ctrl+Shift+S` | Guardar proyecto / guardar como |
| `Ctrl+E` | Exportar |

### Edición

| Teclas | Acción |
| --- | --- |
| `Ctrl+Z` / `Ctrl+Y` | Deshacer / rehacer |
| `Delete` | Eliminar el objeto seleccionado |
| `Ctrl+C` / `Ctrl+V` / `Ctrl+D` | Copiar / pegar / duplicar |
| `Ctrl+]` / `Ctrl+[` | Traer al frente / enviar al fondo |

### Lienzo y vista

| Teclas | Acción |
| --- | --- |
| `Ctrl+Shift+C` | Tamaño del lienzo |
| `Ctrl+Shift+A` | Lienzo según las fotos |
| `Ctrl+M` | Zoom |
| `Ctrl` + rueda | Zoom de la vista |
| botón central + arrastrar | Desplazamiento sin límites |
| rueda, botones de flecha | Desplazamiento de hasta un lienzo por lado |
| `F1` | Referencia de atajos |

### Foto

| Teclas | Acción |
| --- | --- |
| `Ctrl+Shift+H` / `Ctrl+Shift+V` | Espejo horizontal / vertical |
| `Ctrl+Shift+L` / `Ctrl+Shift+R` | Girar a la izquierda / derecha |
| `Shift` + rueda | Rotación libre |

### Modos (mientras la tecla está pulsada)

| Teclas | Acción |
| --- | --- |
| `Z` + rueda | Zoom del contenido dentro del hueco |
| `Z` + arrastrar | Mover el contenido ampliado |
| `C` + arrastrar, `Alt` + arrastrar | Mover la foto dentro de su hueco |
| `R` + arrastrar | Girar la foto |
| `X` | Devolver la foto seleccionada al panel |

<a id="export"></a>

## Exportar

**Archivo → Exportar** (`Ctrl+E`) genera un único archivo PNG o JPEG.

- Escalas predefinidas: 10, 25, 50, 75, 100, 150, 200, 300 y 400 %.
- Control de calidad JPEG, 92 por defecto.
- El diálogo muestra el tamaño en píxeles y una estimación del peso del archivo
  antes de guardar.
- Límites de seguridad: se rechaza un lado superior a 32000 px o una estimación
  mayor de 2 GB, y por encima de 512 MB se pide confirmación.
- El render va en bandas horizontales de 2048 px, así que un lienzo enorme nunca
  necesita un mapa de bits de altura completa en memoria.
- El fondo transparente se conserva en PNG; JPEG no admite transparencia y lo
  aplana.
- Al 100 % la imagen exportada coincide exactamente con el tamaño del lienzo en
  píxeles, lista para imprimir o publicar.

<a id="projects"></a>

## Proyectos, ajustes y registro

- Los proyectos se guardan como `.fcproj` (JSON por dentro); también se acepta
  `.json` para archivos de versiones antiguas.
- Las rutas de las imágenes se guardan **relativas** al archivo del proyecto, así
  que mover la carpeta completa no rompe el collage. Las rutas absolutas se
  conservan como respaldo.
- Si al abrir faltan imágenes, la aplicación ofrece volver a vincularlas
  indicando su nueva carpeta.
- Los 8 últimos proyectos aparecen en **Archivo → Proyectos recientes**.
- Los ajustes se guardan con `QSettings` (registro de Windows): idioma,
  geometría de la ventana, límite de deshacer, escala y calidad de exportación,
  parámetros del ajuste de lienzo y las últimas carpetas usadas.
- El registro está en `%LOCALAPPDATA%\FastCollageForWin\fastcollage.log`, con
  rotación a los 2 MB y 5 copias. La compilación no tiene consola, así que ese
  archivo es el único lugar donde aparece un error inesperado; el diálogo de
  error muestra su ruta.

<a id="layout"></a>

## Estructura del repositorio

```text
main.py                 punto de entrada, registro y arranque de Qt
main.spec               receta de PyInstaller
i18n.py                 los cinco idiomas de la interfaz
core/                   ajustes, presets, tipos de archivo, autoajuste, exportacion, mapa de teclas
canvas/                 escena, elementos de imagen, rejillas, arrastre de bordes
ui/                     ventana principal, panel de imagenes, dialogos
undo/                   pila de deshacer y comandos
assets/icons/           iconos de la interfaz
```

<a id="limits"></a>

## Límites

- Lado del lienzo: 100-10000 px (unos 100 Mpx en el máximo).
- El diálogo de nuevo collage acepta de 1 a 100 fotos por rejilla.
- El autoajuste explora el árbol completo de composiciones hasta 20 fotos; por
  encima de esa cifra usa un esquema simplificado.
- Exportación: como máximo 32000 px por lado y unos 2 GB por archivo.
- Formatos de entrada: PNG, JPG, JPEG, BMP, WEBP.
