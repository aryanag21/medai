from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.allergies import router as allergies_router
from app.api.assessment import router as assessment_router
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.devices import router as devices_router
from app.api.evidence import router as evidence_router
from app.api.health import router as health_router
from app.api.history import router as history_router
from app.api.medications import router as medications_router
from app.api.messages import router as messages_router
from app.api.patient import router as patient_router
from app.api.privacy import router as privacy_router
from app.api.profile import router as profile_router
from app.api.symptoms import router as symptoms_router
from app.api.vitals import router as vitals_router
from app.audit.middleware import AuditLogMiddleware
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="MEDAI Backend", version="0.1.0")

    # Dev-only permissive CORS so the Flutter web client (served from a local dev port) can
    # reach this API. Must be locked down to specific origins before any real deployment —
    # tracked as a Phase 13 security-hardening item.
    if settings.environment == "development":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(AuditLogMiddleware)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(patient_router)
    app.include_router(profile_router)
    app.include_router(history_router)
    app.include_router(allergies_router)
    app.include_router(medications_router)
    app.include_router(conversations_router)
    app.include_router(messages_router)
    app.include_router(symptoms_router)
    app.include_router(evidence_router)
    app.include_router(assessment_router)
    app.include_router(vitals_router)
    app.include_router(devices_router)
    app.include_router(privacy_router)

    @app.get("/download")
    async def download_apk():
        from pathlib import Path
        from fastapi import HTTPException
        from fastapi.responses import FileResponse
        apk_path = Path("C:/Users/aryan/VIT/sem-5/es/medai-apk/medai-android-release-apk/app-release.apk")
        if not apk_path.exists():
            raise HTTPException(status_code=404, detail="APK file not found")
        return FileResponse(apk_path, filename="medai.apk", media_type="application/vnd.android.package-archive")

    @app.get("/clear")
    @app.post("/clear")
    async def clear_chat_web():
        from fastapi.responses import HTMLResponse
        from sqlalchemy import select, delete
        from app.db.session import async_session_factory
        from app.db.models import Conversation, Message, Symptom, Patient

        async with async_session_factory() as db:
            await db.execute(delete(Message))
            await db.execute(delete(Symptom))
            res = await db.execute(select(Patient))
            patient = res.scalars().first()
            if patient:
                new_conv = Conversation(patient_id=patient.id)
                db.add(new_conv)
            await db.commit()

        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>MEDAI - Chat Reset</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; text-align: center; background: #f0fdf4; color: #166534; }
                .box { max-width: 440px; margin: 40px auto; background: white; padding: 32px 24px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
                h2 { margin-top: 0; color: #15803d; }
                p { font-size: 16px; color: #374151; line-height: 1.5; }
                .btn { display: inline-block; margin-top: 16px; background: #16a34a; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px; }
            </style>
        </head>
        <body>
            <div class="box">
                <h2>✨ Chat Reset Successful!</h2>
                <p>All conversation history and previous messages have been cleared on the server.</p>
                <p>You can now go back to the <strong>MEDAI App</strong> and open the consultation screen to start with a clean slate.</p>
                <a href="/download" class="btn">Download Latest APK</a>
            </div>
        </body>
        </html>
        """)

    return app


app = create_app()
