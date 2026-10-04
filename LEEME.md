# Cliente Directo · Agentes gratis

Tus 5 agentes (Buscador, Diseñador, Vendedor, Seguimiento y Gerente) trabajan cada 2 horas en GitHub, usan la IA gratis de Google Gemini, y tú los ves en vivo en tu oficina:
`https://TU-USUARIO.github.io/cliente-directo/`

No consumen tu plan de Claude.

## Instalación (una sola vez, ~15 min)

1. **Cuenta de GitHub:** créala en https://github.com/signup (gratis).
2. **Repositorio:** crea uno nuevo llamado `cliente-directo`, **público** (así los turnos son gratis y sin límite), y sube todos estos archivos. Incluye la carpeta `.github`.
3. **Clave de Gemini:** entra a https://aistudio.google.com/apikey con tu cuenta de Google y toca **Create API key**. Copia la clave.
4. **Guardar claves:** en tu repositorio ve a **Settings → Secrets and variables → Actions → New repository secret** y crea:
   - `GEMINI_API_KEY`: la clave del paso 3 (obligatoria).
   - `GMAIL_USER`: tu correo de Gmail (opcional, para que el Vendedor escriba correos).
   - `GMAIL_APP_PASSWORD`: una contraseña de aplicación de https://myaccount.google.com/apppasswords. Necesita tener activada la verificación en 2 pasos.
   - `NTFY_TOPIC`: un nombre secreto inventado, por ejemplo `clientedirecto-gent-8274` (opcional). Instala la app **ntfy** en tu teléfono y suscríbete a ese nombre: te llega un aviso cuando alguien responde y un resumen a las 8 am y a las 6 pm.
5. **Publicar la oficina:** en **Settings → Pages → Source** elige **GitHub Actions**.
6. **Encender los agentes:** en la pestaña **Actions**, si te lo pide, toca **I understand… enable**. Luego abre **Agentes Cliente Directo → Run workflow** y elige `disenador` para la primera corrida.
7. **Abre tu oficina** en `https://TU-USUARIO.github.io/cliente-directo/`.

## Para mover etapas desde la oficina

En **Ajustes** de la oficina pega una clave de GitHub. Para crearla:

1. Ve a https://github.com/settings/personal-access-tokens/new.
2. En **Repository access** elige solo `cliente-directo`.
3. En **Permissions** pon **Contents: Read and write** y **Actions: Read and write** (para los botones Pausar, Reanudar y Correr ahora).

La clave se guarda solo en tu teléfono o tu computadora.

En el mismo lugar pones tu dirección postal y eliges si los correos se quedan en borrador o salen solos.

## Lo que debes saber

- El repositorio es público, así que cualquiera con el enlace puede ver la lista de negocios. Son datos públicos de los negocios, pero no guardes nada privado ahí. Tus claves sí están protegidas en Secrets.
- El nivel gratis de Gemini tiene límites diarios. Si algún turno dice "No pude buscar", es eso, y el siguiente turno sigue normal.
- Los horarios de GitHub pueden atrasarse unos minutos.
- Los correos solo salen de 8 am a 6 pm (hora de Las Vegas), máximo 15 al día, y siempre con la línea para darse de baja.
