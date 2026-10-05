def test_index_returns_hello_world(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.content == b"Hello, World!"
