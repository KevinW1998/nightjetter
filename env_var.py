from app import CONNECTIONS

if __name__ == "__main__":
    print("__".join(connection.to_envvar_string() for connection in CONNECTIONS))
