import socket


sock = socket.socket()

sock.bind(("127.0.0.1",8080))
sock.listen(5)

while True:
    conn, addr = sock.accept() #阻塞等待
    data = conn.recv(1024)
    print("来自客户端的信息\n", data)
    conn.send(b"HTTP/1.1 200 OK\n")
    conn.close()
