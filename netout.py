"""Вывод NMEA-строк АИС: TCP-сервер (много клиентов) и UDP-рассылка на локальный порт."""
import queue
import socket
import threading


class NmeaOutput:
    def __init__(self, tcp_port=10110, udp_port=10110, udp_host="127.0.0.1", bind="127.0.0.1"):
        self.tcp_port, self.udp_port, self.udp_host, self.bind = tcp_port, udp_port, udp_host, bind
        self.clients = []
        self.lock = threading.Lock()
        self.srv = None
        self.udp = None
        self.sent = 0
        self.error = ""
        self.q = queue.Queue(maxsize=500)

    def start(self):
        threading.Thread(target=self._sender, daemon=True).start()
        try:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError as e:
            self.error += f"UDP: {e}; "
        if self.tcp_port:
            try:
                self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.srv.bind((self.bind, self.tcp_port))
                self.srv.listen(8)
                threading.Thread(target=self._accept, daemon=True).start()
            except OSError as e:
                self.srv = None
                self.error += f"TCP порт {self.tcp_port}: {e}; "

    def _accept(self):
        while self.srv:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                self.clients.append(c)

    @property
    def n_clients(self):
        return len(self.clients)

    def send(self, sentence: str):
        """Не блокирует приёмник: строки уходят клиентам из отдельного потока."""
        try:
            self.q.put_nowait((sentence + "\r\n").encode("ascii"))
        except queue.Full:
            pass

    def _sender(self):
        while True:
            data = self.q.get()
            if data is None:
                return
            self._deliver(data)

    def _deliver(self, data):
        self.sent += 1
        if self.udp and self.udp_port:
            try:
                self.udp.sendto(data, (self.udp_host, self.udp_port))
            except OSError:
                pass
        with self.lock:
            for c in list(self.clients):
                try:
                    c.settimeout(0.2)
                    c.sendall(data)
                except OSError:
                    self.clients.remove(c)
                    try:
                        c.close()
                    except OSError:
                        pass

    def close(self):
        self.q.put(None)
        srv, self.srv = self.srv, None
        if srv:
            srv.close()
        with self.lock:
            for c in self.clients:
                c.close()
            self.clients.clear()
        if self.udp:
            self.udp.close()
