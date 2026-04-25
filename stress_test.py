# stress_test.py
import time
import threading
import sys
import logging
import numpy as np
from matrix_client import MatrixClient

# Suppress verbose logs for a cleaner stress test output
logging.getLogger("p2p_node").setLevel(logging.WARNING)
logging.getLogger("matrix_client").setLevel(logging.WARNING)
logging.getLogger("consistent_hash").setLevel(logging.WARNING)
logging.getLogger("assignment_strategy").setLevel(logging.WARNING)

# Global lock ensures thread-safe, non-interleaved console output
_print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)

def run_client_task(client_id, matrix_size=20, results_list=None, results_lock=None):
    """Simulates a single client submitting a matrix computation."""
    seeds = [
        ('node-0', 'localhost', 50051),
        ('node-1', 'localhost', 50052),
        ('node-2', 'localhost', 50053),
        ('node-3', 'localhost', 50054)
    ]
    client = MatrixClient(client_id, peer_nodes=seeds)

    status = "FAILED"
    duration = 0.0
    error = ""

    if not client.start():
        entry = {"id": client_id, "status": "START_ERR", "time": "0.00s", "result": "N/A", "error": ""}
        if results_list is not None:
            with (results_lock or threading.Lock()):
                results_list.append(entry)
        return

    try:
        # 1. Generate random matrices
        client._matrix_a = np.random.randint(0, 10, size=(matrix_size, matrix_size)).astype(np.float64)
        client._matrix_b = np.random.randint(0, 10, size=(matrix_size, matrix_size)).astype(np.float64)
        client._expected_result = np.matmul(client._matrix_a, client._matrix_b)

        # 2. Submit computation (quiet=True suppresses per-client connectivity
        #    and chunk-distribution prints that interleave during concurrent runs)
        client.submit_computation(quiet=True)

        # 3. Collect and Verify — time the full distributed round-trip
        start_time = time.time()
        collected = client.collect_results(quiet=True)
        if collected:
            duration = time.time() - start_time
            verified = client.verify_results(quiet=True)
            status = "SUCCESS" if verified else "VERIF_FAIL"
        else:
            status = "COMP_FAIL"

    except Exception as e:
        status = "ERROR"
        error = str(e)
    finally:
        client.stop()
        result_preview = "N/A"
        if status == "SUCCESS" and hasattr(client, '_computed_result'):
            res = client._computed_result
            sample = res[0, :3].tolist()   # first-row sample, 3 values
            result_preview = f"{res.shape} Matrix | Sample: {[round(v, 1) for v in sample]}..."

        entry = {
            "id": client_id,
            "status": status,
            "time": f"{duration:.2f}s",
            "result": result_preview,
            "error": error,
        }
        if results_list is not None:
            with (results_lock or threading.Lock()):
                results_list.append(entry)

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        
    NUM_CLIENTS = 10
    MATRIX_DIM = 40

    safe_print("\n" + "="*80)
    safe_print(f"🚀 LAUNCHING CONCURRENT STRESS TEST: {NUM_CLIENTS} CLIENTS")
    safe_print(f"📡 CONNECTED TO: 4 NODES (50051-50054)")
    safe_print(f"📐 MATRIX SIZE : {MATRIX_DIM}×{MATRIX_DIM}")
    safe_print("="*80)
    safe_print("Test in progress... (Detailed logs → p2p_system.log)\n")

    results = []
    results_lock = threading.Lock()   # protect append from concurrent threads
    threads = []

    for i in range(NUM_CLIENTS):
        t = threading.Thread(
            target=run_client_task,
            args=(f"client_{i}", MATRIX_DIM, results, results_lock),
            daemon=True,
        )
        threads.append(t)
        t.start()
        time.sleep(0.15)   # stagger slightly to avoid thundering-herd on startup

    for t in threads:
        t.join()

    # ── Summary table ──────────────────────────────────────────────────────────
    sorted_results = sorted(results, key=lambda x: x["id"])
    success_count  = sum(1 for r in sorted_results if r["status"] == "SUCCESS")

    print("\n" + "="*80)
    print(f"{'CLIENT_ID':<12} | {'STATUS':<12} | {'DURATION':<10} | RESULT PREVIEW")
    print("-" * 80)
    for res in sorted_results:
        status_icon = "✓" if res["status"] == "SUCCESS" else "✗"
        err_note = f"  [{res['error']}]" if res.get("error") else ""
        print(f"{res['id']:<12} | {status_icon} {res['status']:<10} | {res['time']:<10} | {res['result']}{err_note}")
    print("-" * 80)
    print(f"TOTAL: {success_count}/{NUM_CLIENTS} SUCCESSFUL")
    print("="*80)
    print(f"Detailed logs available in: p2p_system.log\n")
