# CLAUDE.md · Catalogator

Antes de nada, lee `CONTEXTO.md`: es el contexto del proyecto y las reglas de trabajo de Adrián (si algo no coincide
con el código, manda el código).

## La fábrica en este proyecto

Toda entrega pasa por la fábrica (Adrián, 20/10/2026): especificación, construcción, revisores independientes y
aprobador antes de `main`. El protocolo, común a todos sus repositorios, está en `.claude/fabrica.md`; aquí, lo propio
de este. Las versiones se publican con una etiqueta (`v...`, `release.yml`), que solo se pone sobre un commit de
`main` ya aprobado: el PC y la Raspberry las instalan solos. `main` también lo usa `nube.yml` cada 10 minutos.

@.claude/fabrica.md

- **Pruebas** (en `.fabrica/pruebas.txt`): `python -m pytest tests -q` en verde (las mismas que `pruebas.yml`).
- **Revisores**: `revisor-codigo` siempre. `revisor-seguridad` si se toca `acceso.py`, `login.py`, `correo.py`,
  `buzon.py`, `nube.py`, `servidor.py`, `admin.py`, `redactor.py` (claves y sesión de Claude), `mediacentral.py`,
  el actualizador y la instalación (`catalogator.py`, `EXE_MIN`, `empaquetar.py`, `instalar_pi.sh`),
  `.github/workflows/` o `.claude/`. `revisor-ux` y `revisor-diseno` si se toca `panel/` o lo que se ve en la ventana.
- **Se escala siempre**: `.github/workflows/` y secretos de GitHub; el actualizador y la instalación
  (`catalogator.py`, `EXE_MIN`, `empaquetar.py`, `instalar_pi.sh`: se instalan solos en el PC y la Raspberry); lo que
  se escribe en MediaCentral (Avid); a quién y qué se manda por correo; el criterio base (`reglas_patrones.md`) y
  lo que es de Adrián (`reglas_extra.md`, `ejemplos.md`, `criterio_cambios.json`, `config.json`, `cola/`).
