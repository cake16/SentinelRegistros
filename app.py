import os
import uuid
from datetime import datetime, timedelta

import requests
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__, static_folder="sources", static_url_path="/sources")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")

# En local usa SQLite; en Render/producción define DATABASE_URL (Postgres de Neon/Supabase).
db_url = os.environ.get("DATABASE_URL", "sqlite:///registro.db")
if db_url.startswith("postgres://"):
    # SQLAlchemy moderno requiere el prefijo postgresql://
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

from dotenv import load_dotenv
load_dotenv()

# Vigencia del link de confirmación (horas)
TOKEN_EXPIRA_HORAS = int(os.environ.get("TOKEN_EXPIRA_HORAS", 24))

# Config de envío de correo vía Brevo (https://www.brevo.com)
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "no-responder@tudominio.com")
BREVO_SENDER_NOMBRE = os.environ.get("BREVO_SENDER_NOMBRE", "Registro")

# URL base pública del servicio (la de Render en producción, o localhost en desarrollo)
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")


class Registro(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    empresa = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    correo = db.Column(db.String(200), nullable=False, unique=True)
    token = db.Column(db.String(64), nullable=False, unique=True)
    confirmado = db.Column(db.Boolean, default=False, nullable=False)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    confirmado_en = db.Column(db.DateTime, nullable=True)

    def token_expirado(self):
        limite = self.creado_en + timedelta(hours=TOKEN_EXPIRA_HORAS)
        return datetime.utcnow() > limite


def enviar_correo_confirmacion(destinatario_nombre, destinatario_correo, token):
    """Envía el correo de confirmación usando la API de Brevo."""
    link_confirmacion = f"{BASE_URL}{url_for('confirmar', token=token)}"

    if not BREVO_API_KEY:
        # Modo desarrollo: si no hay API key configurada, solo lo imprime en consola.
        print(f"[DEV] Link de confirmación para {destinatario_correo}: {link_confirmacion}")
        return True

    payload = {
        "sender": {"name": BREVO_SENDER_NOMBRE, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": destinatario_correo, "name": destinatario_nombre}],
        "subject": "Confirma tu registro",
        "htmlContent": f"""
            <p>Apreciable {destinatario_nombre},</p>
            <p>En nombre de todo el equipo de Sentinel TI, queremos darte las gracias por tomarte el tiempo de visitar 
            nuestro stand y registrarte con nosotros durante el Dell Forum 2026.</p>
            <br>

            <p>Fue un verdadero placer conversar contigo. Esperamos que la información sobre nuestras soluciones de 
            infraestructura y servicios te haya resultado valiosa. Además, te confirmamos que con tu registro 
            ¡Ya estás participando en nuestra rifa!. El sorteo se llevará a cabo al final del día de hoy, así que mantente 
            atento para descubrir si eres uno de los afortunados ganadores. ¡Te deseamos mucha suerte!</p>
            <br>

            <p>Si tienes alguna consulta adicional o te gustaría agendar una reunión para profundizar en cómo podemos apoyar 
            tus proyectos, no dudes en contactarte al correo: contacto@sentinelti.com.mx.</p>
            <br>

            <p>¡Mucho éxito y gracias nuevamente por acompañarnos!</p>
            <br>
            <p>Saludos cordiales</p>
            <p>Equipo de Sentinel TI</p>
            <br>
            <p>Gracias por registrarte. Para confirmar tu correo, da clic en el siguiente enlace:</p>
            <p><a href="{link_confirmacion}">{link_confirmacion}</a></p>
            <p>Este enlace es válido por {TOKEN_EXPIRA_HORAS} horas.</p>
        """,
    }
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=payload,
            headers=headers,
            timeout=10,
        )
        print(f"[BREVO] status={resp.status_code} respuesta={resp.text}")
        return resp.status_code in (200, 201)
    except requests.RequestException as e:
        print(f"Error enviando correo: {e}")
        return False


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/registrar", methods=["POST"])
def registrar():
    nombre = request.form.get("nombre", "").strip()
    empresa = request.form.get("empresa", "").strip()
    telefono = request.form.get("telefono", "").strip()
    correo = request.form.get("correo", "").strip().lower()

    if not nombre or not empresa or not telefono or not correo:
        flash("Todos los campos son obligatorios.")
        return redirect(url_for("index"))

    existente = Registro.query.filter(
        (Registro.correo == correo) | (Registro.telefono == telefono)
    ).first()

    if existente and existente.confirmado:
        if existente.correo == correo:
            flash("Ese correo ya está registrado y confirmado.")
        else:
            flash("Ese teléfono ya está registrado con otro correo.")
        return redirect(url_for("index"))

    if existente and not existente.confirmado:
        flash("Ya tienes un registro pendiente por confirmar. Revisa tu correo.")
        return redirect(url_for("index"))

    nuevo = Registro(nombre=nombre, empresa=empresa, telefono=telefono, correo=correo, token=uuid.uuid4().hex)
    db.session.add(nuevo)
    db.session.commit()

    enviar_correo_confirmacion(nuevo.nombre, nuevo.correo, nuevo.token)
    return render_template("revisa_correo.html", correo=correo)


@app.route("/confirmar/<token>", methods=["GET"])
def confirmar(token):
    registro = Registro.query.filter_by(token=token).first()

    if not registro:
        return render_template("error.html", mensaje="Enlace de confirmación inválido."), 404

    if registro.confirmado:
        return render_template("exito.html", nombre=registro.nombre, ya_confirmado=True)

    if registro.token_expirado():
        return render_template(
            "error.html",
            mensaje="Este enlace ha expirado. Vuelve a registrarte para recibir uno nuevo.",
        ), 400

    registro.confirmado = True
    registro.confirmado_en = datetime.utcnow()
    db.session.commit()

    return render_template("exito.html", nombre=registro.nombre, ya_confirmado=False)


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
