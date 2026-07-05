from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="AI Pre-Sales Engine", version="0.1.0")

    from .middleware.tenant import TenantMiddleware
    from .middleware.auth import AuthMiddleware
    from .middleware.rate_limit import RateLimitMiddleware
    from .routes import leads, calls, tenants, projects, auth, knowledge, whatsapp, health

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TenantMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(leads.router, prefix="/api/leads")
    app.include_router(calls.router, prefix="/api/calls")
    app.include_router(tenants.router, prefix="/api/tenants")
    app.include_router(projects.router, prefix="/api/projects")
    app.include_router(knowledge.router, prefix="/api/knowledge")
    app.include_router(whatsapp.router, prefix="/api/whatsapp")

    return app
