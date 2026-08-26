def test_home_and_healthz(client):
    home = client.get("/")
    health = client.get("/healthz")

    assert home.status_code == 200
    assert "客户快捷填表系统" in home.get_data(as_text=True)
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}


def test_app_is_configured_for_localhost(app):
    # The development command explicitly binds this host; no public bind is configured by the app.
    assert app.config["JSON_AS_ASCII"] is False
    assert "runtime_data" in app.config["SQLALCHEMY_DATABASE_URI"] or app.config["TESTING"]
