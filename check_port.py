import socket
import sys

def check_port(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('0.0.0.0', port))
        print(f"Port {port} is FREE (bind successful)")
        sock.close()
    except OSError as e:
        print(f"Port {port} is BUSY: {e}")

if __name__ == "__main__":
    check_port(8002)
