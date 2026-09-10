# prometheus-mikrotik-btest
Prometheus exporter for bandwith test on Mikrotik devices

## Usage
Change variables, then docker compose up

```
docker compose up -d
```

## Prometheus example config
```
scrape_configs:
  - job_name: 'mikrotik_bandwidth_link'
    metrics_path: /probe
    scrape_interval: 15m      # Scrape frequency / interval
    scrape_timeout: 25s       # Must be higher than TEST_DURATION (10s+) to prevent timeouts!
    
    # Define link pairs to measure in "INITIATOR_ROUTER_IP;TARGET_ROUTER_IP" format
    static_configs:
      - targets:
          - '192.168.88.1;192.168.88.2'
          - '192.168.88.1;192.168.88.3'
          - '10.0.0.1;10.0.0.2'

    relabel_configs:
      # Splits the target string into router and target query parameters
      - source_labels: [__address__]
        regex: (.*);(.*)
        target_label: __param_router
        replacement: $1
      - source_labels: [__address__]
        regex: (.*);(.*)
        target_label: __param_target
        replacement: $2
      
      # Redirects the actual scrape request to the Python Exporter host
      - target_label: __address__
        replacement: '127.0.0.1:9115' # IP and port of the exporter instance
```
