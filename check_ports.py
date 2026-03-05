import socket

def check_port(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', port))
    sock.close()
    return result == 0

print(f"8001: {check_port(8001)}")
print(f"3000: {check_port(3000)}")
