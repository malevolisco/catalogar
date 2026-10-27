/**
 * nube_timbre.gs - El timbre de Catalogator en la nube (NUBE.md, «El timbre»).
 *
 * GitHub, por sí solo, lanza la nube con horas de retraso. Este programita vive en la cuenta de Gmail de
 * Catalogator (script.google.com) y lo ejecuta Google cada minuto: si ha llegado un correo a la dirección
 * con +csv, avisa a GitHub para que arranque la nube en ese momento. No lee ni guarda el contenido de los
 * correos: solo mira si los hay. Quién se contesta y cómo lo decide la nube (nube.py).
 *
 * La llave de GitHub va en Configuración del proyecto -> Propiedades del script -> GITHUB_TOKEN, nunca aquí.
 * Instalación: pegar este código, guardar la llave y ejecutar una vez la función instalar().
 */
const REPOSITORIO = 'malevolisco/catalogar';
const FLUJO = 'nube.yml';
const PRIMERA_VEZ_HORAS = 2;     // la primera vez, mira los correos de las últimas 2 horas

function timbre() {
  const props = PropertiesService.getScriptProperties();
  const llave = props.getProperty('GITHUB_TOKEN');
  if (!llave) {
    console.log('Falta GITHUB_TOKEN en Propiedades del script (NUBE.md)');
    return;
  }
  const yo = Session.getEffectiveUser().getEmail();
  const arroba = yo.indexOf('@');
  const direccion = yo.slice(0, arroba) + '+csv' + yo.slice(arroba);
  const ahora = Math.floor(Date.now() / 1000);
  // ULTIMO_AVISO es la hora (en segundos) del correo más reciente por el que ya se avisó
  const desde = Number(props.getProperty('ULTIMO_AVISO') || (ahora - PRIMERA_VEZ_HORAS * 3600));
  // Gmail entiende after: con segundos; se busca un minuto de más por si un correo llegó justo en el borde,
  // y luego se descartan los que ya se avisaron comparando su hora (así un correo no avisa dos veces)
  const consulta = '(deliveredto:' + direccion + ' OR to:' + direccion + ') after:' + (desde - 60);
  let ultimo = 0;
  GmailApp.search(consulta, 0, 20).forEach(function (hilo) {
    ultimo = Math.max(ultimo, Math.floor(hilo.getLastMessageDate().getTime() / 1000));
  });
  if (ultimo <= desde) {
    return;
  }
  const respuesta = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + REPOSITORIO + '/actions/workflows/' + FLUJO + '/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + llave,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      payload: JSON.stringify({ref: 'main'}),
      muteHttpExceptions: true,
    });
  if (respuesta.getResponseCode() === 204) {
    props.setProperty('ULTIMO_AVISO', String(ultimo));
    console.log('Nube avisada');
  } else {
    // no se guarda la hora: al minuto siguiente se vuelve a intentar
    console.log('GitHub ha contestado ' + respuesta.getResponseCode() + ': revisa la llave (NUBE.md)');
  }
}

function instalar() {
  ScriptApp.getProjectTriggers().forEach(function (reloj) { ScriptApp.deleteTrigger(reloj); });
  ScriptApp.newTrigger('timbre').timeBased().everyMinutes(1).create();
  timbre();
  console.log('Timbre instalado: mira el correo cada minuto');
}
