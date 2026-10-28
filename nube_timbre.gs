/**
 * nube_timbre.gs - El timbre de Catalogator en la nube (NUBE.md, «El timbre»).
 *
 * GitHub, por sí solo, lanza la nube con horas de retraso. Este programita vive en la cuenta de Gmail de
 * Catalogator (script.google.com) y lo ejecuta Google cada minuto. Cambia el texto del issue del timbre, y eso
 * arranca la nube en ese momento (nube.yml, «issues»), cuando:
 *   - ha llegado un correo a la dirección con +csv (la nube los contesta siempre), o
 *   - un correo con adjunto a la dirección normal sigue sin leer a los 15 minutos: la Raspberry no está o no
 *     funciona, y la nube hace de respaldo (nube.py decide si sabe contestarlo).
 * No lee ni guarda el contenido de los correos: solo mira si los hay, si están leídos, su hora y a quién van.
 *
 * La llave solo puede tocar issues: nunca lanzar tareas de GitHub, que con ellas se podría publicar una
 * versión (y el PC y la Raspberry la instalan solos).
 *
 * La llave de GitHub va en Configuración del proyecto -> Propiedades del script -> GITHUB_TOKEN, nunca aquí.
 * Instalación: pegar este código, guardar la llave y ejecutar una vez la función instalar().
 */
const REPOSITORIO = 'malevolisco/catalogar';
const ISSUE = 64;                // «Timbre de la nube (no cerrar ni borrar)»: nube.yml solo atiende a este
const PRIMERA_VEZ_HORAS = 2;     // la primera vez, mira los correos de las últimas 2 horas
const ESPERA_RASPBERRY = 15 * 60;   // como nube.py: sin leer tras 15 minutos, la Raspberry no está
const RESPALDO_HORAS = 24;          // como nube.py: lo que lleva más de un día sin leer no se contesta

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
  const segundos = function (fecha) { return Math.floor(fecha.getTime() / 1000); };

  // +csv: ULTIMO_AVISO es la hora (en segundos) del correo más reciente por el que ya se avisó.
  // Gmail entiende after: con segundos; se busca un minuto de más por si un correo llegó justo en el borde,
  // y luego se descartan los que ya se avisaron comparando su hora (así un correo no avisa dos veces)
  const desde = Number(props.getProperty('ULTIMO_AVISO') || (ahora - PRIMERA_VEZ_HORAS * 3600));
  let ultimo = desde;
  GmailApp.search('(deliveredto:' + direccion + ' OR to:' + direccion + ') after:' + (desde - 60), 0, 20)
    .forEach(function (hilo) { ultimo = Math.max(ultimo, segundos(hilo.getLastMessageDate())); });

  // respaldo: correos con adjunto, sin leer, de entre 15 minutos y un día, que no van a +csv.
  // ULTIMO_RESPALDO es la hora del más reciente por el que ya se avisó (un aviso por correo)
  const desdeRespaldo = Number(props.getProperty('ULTIMO_RESPALDO') || (ahora - RESPALDO_HORAS * 3600));
  let ultimoRespaldo = desdeRespaldo;
  GmailApp.search('in:inbox is:unread has:attachment after:' + (ahora - RESPALDO_HORAS * 3600), 0, 50)
    .forEach(function (hilo) {
      hilo.getMessages().forEach(function (correo) {
        const hora = segundos(correo.getDate());
        const destinos = (correo.getTo() + ',' + correo.getCc()).toLowerCase();
        // un minuto de más: aquí se mira la fecha del correo y en la nube la de llegada, que va unos segundos después
        if (correo.isUnread() && hora > desdeRespaldo && ahora - hora >= ESPERA_RASPBERRY + 60 &&
            destinos.indexOf('+csv@') < 0) {
          ultimoRespaldo = Math.max(ultimoRespaldo, hora);
        }
      });
    });

  if (ultimo <= desde && ultimoRespaldo <= desdeRespaldo) {
    return;
  }
  const texto = 'Este issue es el timbre de Catalogator en la nube (NUBE.md, «El timbre»). Lo cambia solo ' +
    'el programita de Gmail cuando llega un CSV, y eso arranca la nube. No hace falta tocarlo.\n\n' +
    'Último aviso: ' + new Date(ahora * 1000).toISOString();
  const respuesta = UrlFetchApp.fetch('https://api.github.com/repos/' + REPOSITORIO + '/issues/' + ISSUE, {
    method: 'patch',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + llave,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({body: texto}),
    muteHttpExceptions: true,
  });
  if (respuesta.getResponseCode() === 200) {
    props.setProperty('ULTIMO_AVISO', String(ultimo));
    props.setProperty('ULTIMO_RESPALDO', String(ultimoRespaldo));
    console.log(ultimoRespaldo > desdeRespaldo ? 'Nube avisada (respaldo: la Raspberry no ha leído un correo)'
                                               : 'Nube avisada');
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
