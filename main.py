from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import socket
import time
import os

# ================= REQUIRED CONFIGURATION =================
API_USER = os.environ.get('API_USER', 'admin')
API_PASS = os.environ.get('API_PASS', 'secret_password')
API_PORT = int(os.environ.get('API_PORT', 8728))
EXPORTER_PORT = int(os.environ.get('EXPORTER_PORT', 9115))
TEST_DURATION = int(os.environ.get('TEST_DURATION', 10))
# ==========================================================

class RouterOSApi:
    """Raw RouterOS API client implementation for connection and encoding handling"""
    def __init__(self, host, port=8728):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect((host, port))

    def close(self):
        self.sock.close()

    def _encode_length(self, length):
        if length < 0x80:
            return bytes([length])
        elif length < 0x4000:
            return bytes([(length >> 8) | 0x80, length & 0xFF])
        elif length < 0x200000:
            return bytes([(length >> 16) | 0xC0, (length >> 8) & 0xFF, length & 0xFF])
        else:
            return bytes([(length >> 24) | 0xE0, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])

    def write_word(self, word):
        encoded = word.encode('utf-8')
        self.sock.sendall(self._encode_length(len(encoded)) + encoded)

    def write_sentence(self, words):
        for word in words:
            self.write_word(word)
        self.sock.sendall(b'\x00')

    def _read_byte(self):
        b = self.sock.recv(1)
        if not b:
            raise Exception("API connection disconnected")
        return b[0]

    def _read_length(self):
        b = self._read_byte()
        if (b & 0x80) == 0:
            return b
        elif (b & 0xC0) == 0x80:
            return ((b & 0x3F) << 8) | self._read_byte()
        elif (b & 0xE0) == 0xC0:
            return ((b & 0x1F) << 16) | (self._read_byte() << 8) | self._read_byte()
        elif (b & 0xF0) == 0xE0:
            return ((b & 0x0F) << 24) | (self._read_byte() << 16) | (self._read_byte() << 8) | self._read_byte()

    def read_sentence(self):
        sentence = []
        while True:
            length = self._read_length()
            if length == 0:
                break
            data = b''
            while len(data) < length:
                data += self.sock.recv(length - len(data))
            sentence.append(data.decode('utf-8', errors='ignore'))
        return sentence

    def login(self, username, password):
        self.write_sentence(['/login', f'=name={username}', f'=password={password}'])
        response = self.read_sentence()
        if '!done' not in response:
            raise Exception(f"API Authentication Error: {response}")


def run_single_direction_test(api, target_ip, direction):
    """Runs a bandwidth test in a single direction ('rx' or 'tx') and returns the measured speed in bps"""
    last_valid_bps = 0
    cmd = [
        '/tool/bandwidth-test',
        f'=address={target_ip}',
        f'=duration={TEST_DURATION}s',
        '=protocol=udp',
        f'=direction={direction}'
    ]
    api.write_sentence(cmd)

    start_time = time.time()
    
    # Read output sentences until timeout or !done signal
    while time.time() - start_time < (TEST_DURATION + 5):
        try:
            sentence = api.read_sentence()
        except socket.timeout:
            break

        if not sentence:
            continue

        # Inspect all key=value pairs in the returned API sentence
        for word in sentence:
            if '=' in word:
                parts = word.lstrip('=').split('=')
                if len(parts) == 2:
                    key, val = parts[0], parts[1]
                    # Matches keys like: rx-current, rx-10sec-average, tx-current, etc.
                    if key.startswith(direction) and val.isdigit():
                        current_val = int(val)
                        if current_val > 0:
                            last_valid_bps = current_val

        if '!done' in sentence or '!trap' in sentence:
            break

    return last_valid_bps


def run_btest(router_ip, target_ip):
    """Executes RX and TX bandwidth tests using fresh API connections to ensure reliability"""
    rx_bps, tx_bps = 0, 0

    # 1. Step: Measure RX (Download) using a dedicated connection
    try:
        api_rx = RouterOSApi(router_ip, API_PORT)
        api_rx.login(API_USER, API_PASS)
        rx_bps = run_single_direction_test(api_rx, target_ip, 'rx')
        api_rx.close()
    except Exception as e:
        print(f"[Test Error RX] Router: {router_ip} -> Target: {target_ip} | Error: {e}")

    time.sleep(1)

    # 2. Step: Measure TX (Upload) using a dedicated fresh connection
    try:
        api_tx = RouterOSApi(router_ip, API_PORT)
        api_tx.login(API_USER, API_PASS)
        tx_bps = run_single_direction_test(api_tx, target_ip, 'tx')
        api_tx.close()
    except Exception as e:
        print(f"[Test Error TX] Router: {router_ip} -> Target: {target_ip} | Error: {e}")

    print(f"[Test Finished] Router: {router_ip} -> Target: {target_ip} | RX: {rx_bps / 1_000_000:.2f} Mbps | TX: {tx_bps / 1_000_000:.2f} Mbps")

    return rx_bps, tx_bps


class MetricsHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests in Prometheus-compatible exposition format"""
    def do_GET(self):
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == '/probe':
            params = parse_qs(parsed_url.query)
            
            router_ip = params.get('router', [None])[0]
            target_ip = params.get('target', [None])[0]
            
            if not router_ip or not target_ip:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing 'router' or 'target' URL parameters!\n")
                return

            rx_bps, tx_bps = run_btest(router_ip, target_ip)

            # Prometheus TSDB text output format
            response_content = (
                f'# HELP mikrotik_btest_rx_bps Measured RX speed in bps\n'
                f'# TYPE mikrotik_btest_rx_bps gauge\n'
                f'mikrotik_btest_rx_bps{{router="{router_ip}",target="{target_ip}"}} {rx_bps}\n\n'
                f'# HELP mikrotik_btest_tx_bps Measured TX speed in bps\n'
                f'# TYPE mikrotik_btest_tx_bps gauge\n'
                f'mikrotik_btest_tx_bps{{router="{router_ip}",target="{target_ip}"}} {tx_bps}\n'
            ).encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.end_headers()
            self.wfile.write(response_content)
            
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found - Use the /probe?router=X&target=Y endpoint!\n")


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', EXPORTER_PORT), MetricsHandler)
    print(f"Multi-Target Prometheus Exporter running on port :{EXPORTER_PORT}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
