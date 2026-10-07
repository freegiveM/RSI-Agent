"""Synthetic fixture used only for RSI-Agent review pipeline tests."""


def find_user(connection, request):
    user_id = request.args.get("id", "")
    query = "SELECT * FROM users WHERE id = '" + user_id + "'"
    return connection.execute(query).fetchone()
