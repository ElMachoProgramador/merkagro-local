# MERKAGRO Local

Aplicación para Windows de facturas pendientes por pagar y reporte de turno. Funciona sin internet para registrar datos. Solo necesita conexión para buscar y descargar actualizaciones.

## Descargar

[Descargar la última versión](https://github.com/ElMachoProgramador/merkagro-local/releases/latest/download/MerkagroLocal.exe).

El ejecutable público comienza sin información comercial. Los Excel, facturas y bases de datos de usuarios no se publican en este repositorio. En equipos que ya tienen MERKAGRO, se conserva `%LOCALAPPDATA%\MerkagroLocal`.

Quien tenga la versión 1.0 debe reemplazar una sola vez su ejecutable por la versión 1.1 o posterior. Después de eso, el programa consulta este repositorio al abrir y cada cuatro horas, descarga publicaciones verificadas y las instala al cerrar mediante el botón «Cerrar programa». Vuelve a abrirse al terminar. Cerrar solo la pestaña no cierra el servicio local.

## Funciones

- Facturas y abonos por proveedor, pesos colombianos sin decimales, fecha corregible.
- Saldos automáticos y consulta del historial.
- Conteo por denominaciones, solo cantidades editables.
- Recaudo = billetes de 100.000 y 50.000. Base = resto del efectivo.
- Base local SQLite y copias de seguridad.
- Actualizaciones HTTPS verificadas por firma Ed25519, tamaño y SHA-256.

## Publicar una actualización

1. Modificar el código y aumentar `VERSION` en `updater.py` (ejemplo: `1.2.0`).
2. Ejecutar las pruebas: `python -m unittest test_updates.py`.
3. Construir en Windows: `python build_release.py` (requiere `pip install -r requirements-build.txt`). El resultado es `release-public/MerkagroLocal.exe`, sin datos comerciales.
4. En el equipo del propietario, firmar: `python preparar_actualizaciones.py --repo ElMachoProgramador/merkagro-local --exe release-public/MerkagroLocal.exe --version 1.2.0 --out release-public`.
5. Publicar un Release estable con etiqueta `v1.2.0` y adjuntar **ambos** archivos: `MerkagroLocal.exe` y `update.json`. No sustituir archivos dentro de versiones ya publicadas: usar un número nuevo.

Cambiar código en GitHub no actualiza directamente a los usuarios. Es necesaria una publicación estable con ejecutable y manifiesto firmado. Este paso evita distribuir cambios incompletos o sin verificar.

La clave privada de publicación está fuera del repositorio, en el equipo del propietario. No se incluye en el programa, GitHub, copias de usuarios ni artefactos públicos. Debe respaldarse privadamente para poder firmar versiones futuras. La clave pública está en `assets/update_channel.json`. No reemplazarla al construir versiones nuevas.

## Compilación automática

GitHub Actions prueba y construye el ejecutable al cambiar `main` o al ejecutar el flujo manualmente. El artefacto resultante es candidato a publicación; el propietario lo firma antes de publicar. No se almacenan credenciales de firma en GitHub.

## Datos y recuperación

El programa guarda los datos en `%LOCALAPPDATA%\MerkagroLocal`, fuera del ejecutable. Antes de actualizar crea una copia local de la base. Una actualización reemplaza únicamente el programa, no las facturas ni los conteos. Si falla el reemplazo, intenta restaurar el ejecutable anterior y registra el resultado en `actualizaciones/instalacion.log`.

Los equipos desconectados siguen funcionando; buscan actualizaciones cuando vuelva la conexión y se abra el programa o llegue la siguiente comprobación. El programa debe estar encendido para comprobar el canal.

Repositorio oficial: https://github.com/ElMachoProgramador/merkagro-local
Referencia del servicio de publicaciones: https://docs.github.com/en/rest/releases/releases
