import os
import time
import logging
import requests
import psutil
import backoff
import sys



API_BASE_URL = os.getenv('K8INTEL_API_URL') # No default, let's check it.
AGENT_API_KEY = os.getenv('K8INTEL_AGENT_API_KEY')
CLUSTER_ID = os.getenv('K8INTEL_CLUSTER_ID')
POLL_INTERVAL = int(os.getenv('K8INTEL_POLL_INTERVAL', '30'))

# --- Alerting Thresholds ---
CPU_THRESHOLD = float(os.getenv('K8INTEL_CPU_THRESHOLD', '90.0'))
MEMORY_THRESHOLD = float(os.getenv('K8INTEL_MEMORY_THRESHOLD', '85.0'))

# --- Logging Setup ---
# This sets up a simple logger to print info to the console.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def validate_config():
    """Checks for all required environment variables and exits if any are missing."""
    logging.info("Validating required configuration...")
    
    # This dictionary should be defined INSIDE the function.
    required_vars = {
        "K8INTEL_API_URL": API_BASE_URL,
        "K8INTEL_AGENT_API_KEY": AGENT_API_KEY,
        "K8INTEL_CLUSTER_ID": CLUSTER_ID
    }
    
    missing_vars = [name for name, value in required_vars.items() if not value]
    
    if missing_vars:
        logging.error("FATAL: The following required environment variables are not set: %s", ", ".join(missing_vars))
        sys.exit(1)

    try:
        int(CLUSTER_ID)
    except (ValueError, TypeError):
        logging.error(f"FATAL: K8INTEL_CLUSTER_ID must be a valid integer. Found: '{CLUSTER_ID}'")
        sys.exit(1)
    
    logging.info("Configuration validation successful.")


@backoff.on_exception(backoff.expo,
                      requests.exceptions.RequestException,
                      max_tries=5,
                      giveup=lambda e: e.response is not None and 400 <= e.response.status_code < 500,
                      jitter=backoff.full_jitter)
def post_to_api(endpoint, payload):
    """
    Generic and resilient function to POST data to a given API endpoint with retry logic.
    """

    url = f"{API_BASE_URL}/{endpoint}"
    headers = {
        "X-Api-Key": AGENT_API_KEY,
        "Content-Type": "application/json"
    }

    logging.info(f"Attempting to post to {url}...")
    response = requests.post(url, json=payload, headers=headers, verify=False, timeout=15)
    response.raise_for_status() 
    logging.info(f"Successfully posted data to {endpoint}: {payload}")



def post_metric(metric_type, value):
    """
    Constructs and sends a single metric payload to the backend API.
    C# Analogy: A private async Task PostMetric(string metricType, double value) method.
    """
    if not AGENT_API_KEY or not CLUSTER_ID:
        logging.warning("API Key or Cluster ID is not set. Cannot send metric.")
        return

    # The URL for the metrics endpoint.
    url = f"{API_BASE_URL}/metrics"
    
    # Python Dictionaries are like a Dictionary<string, object> and are automatically serialized to JSON by 'requests'.
    payload = {
        "clusterId": int(CLUSTER_ID),
        "metricType": metric_type,
        "value": value
    }
    
    # Headers are also a dictionary. This is where we put our API Key for authentication.
    # We will use "X-Api-Key" as the header name. It's a common convention.
    headers = {
        "X-Api-Key": AGENT_API_KEY
    }

    # 'try/except' is Python's version of 'try/catch'.
    try:
        # requests.post sends the HTTP POST request. We turn off SSL verification for local dev with dotnet's self-signed cert.
        # In production, 'verify' should be True and the certificate should be valid.
        response = requests.post(url, json=payload, headers=headers, verify=False, timeout=10)
        
        # This will throw an exception for any error response (like 401, 404, 500).
        response.raise_for_status() 
        
        logging.info(f"Successfully posted metric: {metric_type} = {value}")

    except requests.exceptions.RequestException as e:
        # C# Analogy: catch (HttpRequestException e)
        logging.error(f"Failed to post metric for {metric_type}: {e}")


def main_loop():
    """The main loop of the agent that runs forever."""
    logging.info(f"K8Intel Agent starting up. Target API: {API_BASE_URL}, Cluster ID: {CLUSTER_ID}")
    logging.info(f"Alerting Thresholds -> CPU: {CPU_THRESHOLD}%, Memory: {MEMORY_THRESHOLD}%")

    while True:
        try:
                    
            # --- 1. Get initial I/O counters ---
            disk_io_start = psutil.disk_io_counters()
            net_io_start = psutil.net_io_counters()
            
            # --- 2. Get CPU and Memory usage ---
            # psutil.cpu_percent() blocks for the interval, giving us a time delay.
            cpu_usage = psutil.cpu_percent(interval=1)
            memory_info = psutil.virtual_memory()
            memory_usage = memory_info.percent

            # --- 3. Get final I/O counters after the delay ---
            disk_io_end = psutil.disk_io_counters()
            net_io_end = psutil.net_io_counters()
            
            # --- 4. Calculate I/O rates (bytes per second) ---
            disk_read_bps = disk_io_end.read_bytes - disk_io_start.read_bytes
            disk_write_bps = disk_io_end.write_bytes - disk_io_start.write_bytes
            net_sent_bps = net_io_end.bytes_sent - net_io_start.bytes_sent
            net_recv_bps = net_io_end.bytes_recv - net_io_start.bytes_recv

            # Create a dictionary of all metrics to post
            all_metrics = {
                "CPU": cpu_usage,
                "Memory": memory_usage,
                "DiskReadBPS": disk_read_bps,
                "DiskWriteBPS": disk_write_bps,
                "NetSentBPS": net_sent_bps,
                "NetRecvBPS": net_recv_bps
            }

            logging.info(f"Collected Metrics: {all_metrics}")
            
            # === END: EXPANDED METRIC COLLECTION ===

            # Loop through all collected metrics and post them
            for metric_type, value in all_metrics.items():
                # Post the metric
                payload = {"clusterId": int(CLUSTER_ID), "metricType": metric_type, "value": value}
                post_to_api("metrics", payload)
                
                # Check for alert conditions based on the metric we just processed
                if metric_type == "CPU" and value > CPU_THRESHOLD:
                    alert_payload = {
                        "clusterId": int(CLUSTER_ID),
                        "severity": "Critical",
                        "message": f"High CPU usage detected: {value:.2f}% (Threshold: {CPU_THRESHOLD}%)"
                    }
                    post_to_api("alerts", alert_payload)
                elif metric_type == "Memory" and value > MEMORY_THRESHOLD:
                    alert_payload = {
                        "clusterId": int(CLUSTER_ID),
                        "severity": "Warning",
                        "message": f"High Memory usage detected: {value:.2f}% (Threshold: {MEMORY_THRESHOLD}%)"
                    }
                    post_to_api("alerts", alert_payload)

        except Exception as e:
            logging.error(f"An unexpected error occurred in the main loop: {e}")
        
        logging.info(f"Sleeping for {POLL_INTERVAL} seconds...")
        # Adjust sleep time to account for the 1-second collection interval
        # This makes the total loop time closer to the desired POLL_INTERVAL.
        time.sleep(max(0, POLL_INTERVAL - 1))


if __name__ == "__main__":
    main_loop()