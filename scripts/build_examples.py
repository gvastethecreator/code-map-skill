#!/usr/bin/env python3
"""Build example infrastructure maps for the README gallery."""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "SKILLS" / "maintain-code-map" / "assets" / "codemap-template.html"
EXAMPLES = ROOT / "examples"
DOCS_EXAMPLES = ROOT / "docs" / "examples"
STAMP = "2026-08-16T22:00:00Z"
COMMIT = "0" * 40


def node(node_id: str, path: str, kind: str, boundary: str, symbol: str, tests: list[str] | None = None) -> dict:
    return {
        "id": node_id,
        "path": path,
        "type": kind,
        "boundary": boundary,
        "entrypoints": [f"{path}:{symbol}"],
        "tests": tests or [],
        "callers": [],
        "callees": [],
        "evidence": {
            "status": "verified",
            "locations": [{"path": path, "symbol": symbol}],
        },
    }


def edge(src: str, dst: str, kind: str, path: str, symbol: str, unknown: bool = False) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": kind,
        "evidence": {
            "status": "unknown" if unknown else "verified",
            "locations": [] if unknown else [{"path": path, "symbol": symbol}],
        },
    }


def wire(model: dict) -> dict:
    by_id = {item["id"]: item for item in model["nodes"]}
    for item in model["nodes"]:
        item["callers"] = []
        item["callees"] = []
    for link in model["edges"]:
        by_id[link["from"]]["callees"].append({"id": link["to"], "type": link["type"]})
        by_id[link["to"]]["callers"].append({"id": link["from"], "type": link["type"]})
    for item in model["nodes"]:
        item["callers"].sort(key=lambda value: (value["id"], value["type"]))
        item["callees"].sort(key=lambda value: (value["id"], value["type"]))
    model["nodes"].sort(key=lambda value: value["id"])
    model["edges"].sort(key=lambda value: (value["from"], value["to"], value["type"]))
    return model


def model(title: str, scope: list[str], nodes: list[dict], edges: list[dict], flows: list[dict]) -> dict:
    return wire(
        {
            "generated_at": STAMP,
            "generated_from_commit": COMMIT,
            "scope": scope,
            "nodes": nodes,
            "edges": edges,
            "flows": flows,
        }
    )


EXAMPLES_SPEC = [
    {
        "slug": "checkout-api",
        "title": "northstar-checkout",
        "caption": "Layered checkout API",
        "blurb": "Gateway, domain services, Postgres, and Stripe.",
        "data": model(
            "northstar-checkout",
            ["apps/checkout", "services", "infra"],
            [
                node("edge-gateway", "apps/checkout/gateway.ts", "interface", "Edge", "handleRequest", ["apps/checkout/gateway.test.ts"]),
                node("checkout-api", "apps/checkout/api/server.ts", "interface", "Application", "createServer", ["apps/checkout/api/server.test.ts"]),
                node("catalog", "services/catalog/index.ts", "service", "Domain", "getProduct"),
                node("pricing", "services/pricing/index.ts", "service", "Domain", "quote"),
                node("payments", "services/payments/charge.ts", "service", "Domain", "chargeCard", ["services/payments/charge.test.ts"]),
                node("orders", "services/orders/store.ts", "service", "Domain", "placeOrder"),
                node("postgres", "infra/postgres/schema.sql", "database", "Data", "orders"),
                node("stripe", "node_modules/stripe", "external", "External", "charges.create"),
            ],
            [
                edge("edge-gateway", "checkout-api", "calls", "apps/checkout/gateway.ts", "proxyCheckout"),
                edge("checkout-api", "catalog", "calls", "apps/checkout/api/catalog.ts", "loadProduct"),
                edge("checkout-api", "pricing", "calls", "apps/checkout/api/quote.ts", "priceCart"),
                edge("checkout-api", "payments", "calls", "apps/checkout/api/pay.ts", "takePayment"),
                edge("checkout-api", "orders", "calls", "apps/checkout/api/orders.ts", "commitOrder"),
                edge("catalog", "postgres", "reads", "services/catalog/index.ts", "selectProduct"),
                edge("pricing", "postgres", "reads", "services/pricing/index.ts", "selectPrice"),
                edge("orders", "postgres", "writes", "services/orders/store.ts", "insertOrder"),
                edge("payments", "stripe", "calls", "services/payments/charge.ts", "createCharge"),
            ],
            [
                {
                    "id": "place-order",
                    "trigger": "POST /checkout",
                    "steps": ["edge-gateway", "checkout-api", "payments", "stripe"],
                    "outcome": "Charge captured through Stripe before the order write",
                }
            ],
        ),
    },
    {
        "slug": "event-ingest",
        "title": "harbor-ingest",
        "caption": "Event-driven warehouse",
        "blurb": "Publish/subscribe workers, a queue, and side effects.",
        "data": model(
            "harbor-ingest",
            ["apps/ingest", "workers", "infra"],
            [
                node("ingest-api", "apps/ingest/http.ts", "interface", "Intake", "receiveShipment", ["apps/ingest/http.test.ts"]),
                node("schema-guard", "apps/ingest/validate.ts", "service", "Intake", "assertPayload"),
                node("event-bus", "infra/nats/subjects.ts", "queue", "Messaging", "SHIPMENT_RECEIVED"),
                node("stock-worker", "workers/stock/handler.ts", "service", "Workers", "applyStock", ["workers/stock/handler.test.ts"]),
                node("notify-worker", "workers/notify/handler.ts", "service", "Workers", "sendNotice"),
                node("warehouse-db", "infra/postgres/warehouse.sql", "database", "Data", "stock_levels"),
                node("audit-log", "apps/ingest/audit.ts", "module", "Observability", "recordEvent"),
                node("mailgun", "node_modules/mailgun.js", "external", "External", "messages.create"),
            ],
            [
                edge("ingest-api", "schema-guard", "calls", "apps/ingest/http.ts", "validateBody"),
                edge("schema-guard", "event-bus", "publishes", "apps/ingest/validate.ts", "publishShipment"),
                edge("schema-guard", "audit-log", "writes", "apps/ingest/validate.ts", "writeAudit"),
                edge("stock-worker", "event-bus", "subscribes", "workers/stock/handler.ts", "onShipment"),
                edge("notify-worker", "event-bus", "subscribes", "workers/notify/handler.ts", "onShipment"),
                edge("stock-worker", "warehouse-db", "writes", "workers/stock/handler.ts", "upsertStock"),
                edge("notify-worker", "mailgun", "calls", "workers/notify/handler.ts", "emailOps"),
            ],
            [
                {
                    "id": "receive-shipment",
                    "trigger": "POST /shipments",
                    "steps": ["ingest-api", "schema-guard", "event-bus"],
                    "outcome": "Validated shipment published onto the bus",
                }
            ],
        ),
    },
    {
        "slug": "desktop-studio",
        "title": "lumen-studio",
        "caption": "Desktop host and renderer",
        "blurb": "Electron main, preload bridge, UI, and a sidecar.",
        "data": model(
            "lumen-studio",
            ["src/main", "src/preload", "src/renderer", "sidecar"],
            [
                node("electron-main", "src/main/index.ts", "service", "Host", "createWindow", ["src/main/index.test.ts"]),
                node("ipc-router", "src/main/ipc.ts", "module", "Bridge", "registerIpc"),
                node("preload", "src/preload/index.ts", "interface", "Bridge", "exposeApi"),
                node("renderer", "src/renderer/app.tsx", "module", "UI", "App"),
                node("timeline", "src/renderer/timeline.tsx", "module", "UI", "TimelineView"),
                node("ffmpeg-sidecar", "sidecar/ffmpeg/run.ts", "service", "Native", "exportClip"),
                node("user-store", "src/main/store.ts", "database", "Data", "readProject"),
                node("os-fs", "src/main/fs.ts", "external", "External", "readFile"),
            ],
            [
                edge("renderer", "timeline", "imports", "src/renderer/app.tsx", "TimelineView"),
                edge("renderer", "preload", "calls", "src/renderer/desktop.ts", "invokeExport"),
                edge("preload", "ipc-router", "calls", "src/preload/index.ts", "exportClip"),
                edge("ipc-router", "electron-main", "calls", "src/main/ipc.ts", "handleExport"),
                edge("electron-main", "ffmpeg-sidecar", "calls", "src/main/export.ts", "runSidecar"),
                edge("electron-main", "user-store", "writes", "src/main/store.ts", "saveProject"),
                edge("electron-main", "os-fs", "reads", "src/main/fs.ts", "readMedia"),
                edge("renderer", "user-store", "reads", "src/renderer/project.ts", "hydrate"),
            ],
            [
                {
                    "id": "export-clip",
                    "trigger": "Export button",
                    "steps": ["renderer", "preload", "ipc-router", "electron-main", "ffmpeg-sidecar"],
                    "outcome": "Sidecar writes a media file",
                }
            ],
        ),
    },
    {
        "slug": "edge-platform",
        "title": "ember-edge",
        "caption": "Edge worker platform",
        "blurb": "Worker, Durable Objects, D1/KV/R2, and one unknown hop.",
        "data": model(
            "ember-edge",
            ["src", "bindings"],
            [
                node("worker", "src/index.ts", "interface", "Edge", "fetch", ["src/index.test.ts"]),
                node("auth-do", "src/auth-do.ts", "service", "Durable Objects", "AuthObject"),
                node("rate-do", "src/rate-do.ts", "service", "Durable Objects", "RateLimiter"),
                node("sessions-d1", "bindings/d1/schema.sql", "database", "Storage", "sessions"),
                node("config-kv", "bindings/kv/config.ts", "database", "Storage", "CONFIG"),
                node("media-r2", "bindings/r2/media.ts", "database", "Storage", "MEDIA"),
                node("email-queue", "bindings/queue/email.ts", "queue", "Messaging", "EMAIL"),
                node("billing-legacy", "vendor/billing", "external", "External", "createInvoice"),
            ],
            [
                edge("worker", "auth-do", "calls", "src/index.ts", "getAuth"),
                edge("auth-do", "sessions-d1", "writes", "src/auth-do.ts", "persistSession"),
                edge("worker", "rate-do", "calls", "src/index.ts", "checkLimit"),
                edge("worker", "sessions-d1", "reads", "src/session.ts", "selectSession"),
                edge("worker", "sessions-d1", "writes", "src/session.ts", "upsertSession"),
                edge("worker", "config-kv", "reads", "src/config.ts", "getFlag"),
                edge("worker", "media-r2", "writes", "src/media.ts", "putObject"),
                edge("worker", "email-queue", "publishes", "src/mail.ts", "enqueueWelcome"),
                edge("worker", "billing-legacy", "calls", "src/billing.ts", "createInvoice", unknown=True),
            ],
            [
                {
                    "id": "sign-in",
                    "trigger": "POST /auth/magic-link",
                    "steps": ["worker", "auth-do", "sessions-d1"],
                    "outcome": "Durable Object writes the signed-in session",
                }
            ],
        ),
    },
]


def render_example(slug: str, title: str, data: dict) -> Path:
    folder = EXAMPLES / slug
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "codemap.json"
    html_path = folder / "codemap.html"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    content = TEMPLATE.read_text(encoding="utf-8")
    if "__CODEMAP_DATA__" not in content or "__REPO_NAME__" not in content:
        raise SystemExit("template placeholders are missing")
    content = content.replace("__CODEMAP_DATA__", compact).replace("__REPO_NAME__", html.escape(title))
    html_path.write_text(content, encoding="utf-8")
    docs_html = DOCS_EXAMPLES / slug / "codemap.html"
    docs_html.parent.mkdir(parents=True, exist_ok=True)
    docs_html.write_text(content, encoding="utf-8")
    return html_path


def main() -> int:
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    catalog = []
    for spec in EXAMPLES_SPEC:
        path = render_example(spec["slug"], spec["title"], spec["data"])
        catalog.append(
            {
                "slug": spec["slug"],
                "title": spec["title"],
                "caption": spec["caption"],
                "blurb": spec["blurb"],
                "html": path.relative_to(ROOT).as_posix(),
                "docs_html": f"docs/examples/{spec['slug']}/codemap.html",
                "json": f"examples/{spec['slug']}/codemap.json",
                "nodes": len(spec["data"]["nodes"]),
                "edges": len(spec["data"]["edges"]),
            }
        )
        print(f"built {spec['slug']}: {len(spec['data']['nodes'])} nodes, {len(spec['data']['edges'])} edges")
    (EXAMPLES / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
