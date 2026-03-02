import argparse
import grpc
import numpy as np
import matrix_pb2
import matrix_pb2_grpc
import time
import threading


def probe_workers(workers, timeout=2):
    """
    Check which workers are actually available.
    Returns tuple: (available_workers, unavailable_workers)
    """
    available = []
    unavailable = []
    
    def check_worker(w):
        try:
            channel = grpc.insecure_channel(w)
            grpc.channel_ready_future(channel).result(timeout=timeout)
            channel.close()
            available.append(w)
        except Exception:
            unavailable.append(w)
    
    threads = []
    for w in workers:
        t = threading.Thread(target=check_worker, args=(w,))
        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()
    
    return available, unavailable


def get_matrix_from_user():
    print("Choose input method:")
    print("1) Random matrices")
    print("2) Manual input")
    choice = input("Enter 1 or 2: ").strip()

    if choice == "1":
        r = int(input("Rows for A: ") or 6)
        c = int(input("Cols for A / Rows for B: ") or 6)
        c2 = int(input("Cols for B: ") or c)
        A = np.random.randint(1, 10, (r, c))
        B = np.random.randint(1, 10, (c, c2))
        return A, B

    # manual
    rows = int(input("Rows for A: ").strip())
    cols = int(input("Cols for A / Rows for B: ").strip())
    colsb = int(input("Cols for B: ").strip())

    print(f"Enter {rows} rows for A, each row as {cols} numbers separated by spaces")
    A_rows = []
    for i in range(rows):
        while True:
            line = input(f"A row {i}: ").strip()
            parts = line.split()
            if len(parts) != cols:
                print(f"Expected {cols} numbers")
                continue
            A_rows.append([float(x) for x in parts])
            break

    print(f"Enter {cols} rows for B, each row as {colsb} numbers separated by spaces")
    B_rows = []
    for i in range(cols):
        while True:
            line = input(f"B row {i}: ").strip()
            parts = line.split()
            if len(parts) != colsb:
                print(f"Expected {colsb} numbers")
                continue
            B_rows.append([float(x) for x in parts])
            break

    A = np.array(A_rows)
    B = np.array(B_rows)
    return A, B


def compute_distributed(A, B, workers, retries=3, rpc_timeout=10):
    """
    Distributed matrix multiplication with self-sustainable fault tolerance.
    Works with any number of available workers (1 or more).
    """
    overall_start = time.time()
    rows = A.shape[0]
    
    if rows == 0:
        return np.empty((0, B.shape[1])), {}
    
    print("\n" + "="*80)
    print("CHECKING WORKER AVAILABILITY")
    print("="*80)
    print(f"Probing {len(workers)} workers: {workers}")
    
    # Probe for available workers
    available_workers, unavailable_workers = probe_workers(workers)
    
    print(f"\n✓ Available workers ({len(available_workers)}): {available_workers}")
    if unavailable_workers:
        print(f"✗ Unavailable workers ({len(unavailable_workers)}): {unavailable_workers}")
    
    if not available_workers:
        raise RuntimeError(f"No workers available! All workers are down: {workers}")
    
    print(f"\nProceeding with {len(available_workers)} available worker(s)")
    print("="*80 + "\n")
    
    # Dynamic chunk creation based on available workers only
    active_workers = available_workers
    num_workers = len(active_workers)
    nchunks = min(rows, max(1, num_workers))
    
    # Create chunks
    chunks = []
    chunk_size = rows // nchunks
    remainder = rows % nchunks
    
    start = 0
    for i in range(nchunks):
        end = start + chunk_size
        if i < remainder:  # Distribute remainder rows
            end += 1
        chunks.append((start, end))
        start = end
    
    print(f"Total rows to compute: {rows}")
    print(f"Active workers: {num_workers}")
    print(f"Chunks created: {len(chunks)}")
    print(f"Initial distribution:")
    for idx, (s, e) in enumerate(chunks):
        row_list = ", ".join(str(i) for i in range(s, e))
        assigned_worker = active_workers[idx % len(active_workers)]
        print(f"  Chunk {idx+1}: Rows {row_list} → {assigned_worker}")
    print()
    
    results = {}
    handled_by = {}
    worker_times = {}
    failed_chunks = []
    
    # Process each chunk with fault tolerance
    for chunk_idx, (start, end) in enumerate(chunks):
        success = False
        last_err = None
        
        # Try to assign to a worker (with retries and fallback)
        for attempt in range(retries + 1):
            # Get list of workers to try (rotate through available ones)
            worker_order = [active_workers[(chunk_idx + attempt + i) % len(active_workers)] 
                          for i in range(len(active_workers))]
            
            for w in worker_order:
                try:
                    chunk_send_start = time.time()
                    channel = grpc.insecure_channel(w)
                    
                    # Verify worker is ready
                    grpc.channel_ready_future(channel).result(timeout=2)
                    
                    stub = matrix_pb2_grpc.MatrixServiceStub(channel)
                    request = matrix_pb2.MatrixRequest(
                        start_row=start,
                        end_row=end,
                        matrixA=A.flatten().tolist(),
                        matrixB=B.flatten().tolist(),
                        rowsA=A.shape[0],
                        colsA=A.shape[1],
                        colsB=B.shape[1],
                    )
                    
                    response = stub.ComputeRows(request, timeout=rpc_timeout)
                    chunk_time_ms = (time.time() - chunk_send_start) * 1000
                    
                    part = np.array(response.result).reshape(response.rows, response.cols)
                    results[start] = part
                    handled_by[start] = (w, start, end)
                    
                    # Store worker computation time
                    worker_computation_ms = response.computation_time_ms
                    if w not in worker_times:
                        worker_times[w] = []
                    
                    row_list = ", ".join(str(i) for i in range(start, end))
                    worker_times[w].append({
                        'rows': (start, end),
                        'row_list': row_list,
                        'computation_ms': worker_computation_ms,
                        'rpc_ms': chunk_time_ms
                    })
                    
                    print(f"✓ Chunk {chunk_idx+1}: Rows {row_list}")
                    print(f"  Worker: {w}")
                    print(f"  Computation time: {worker_computation_ms:.2f} ms")
                    print(f"  RPC time: {chunk_time_ms:.2f} ms\n")
                    
                    success = True
                    break
                
                except Exception as e:
                    last_err = e
                    print(f"✗ Attempt {attempt+1}: Worker {w} failed for chunk {chunk_idx+1} - {str(e)[:50]}")
                    continue
            
            if success:
                break
            
            # Backoff before retry
            if attempt < retries:
                wait_time = 1 + attempt * 1.5
                print(f"  Retrying in {wait_time:.1f} seconds...\n")
                time.sleep(wait_time)
        
        if not success:
            print(f"\n✗ FAILED: Chunk {chunk_idx+1} (Rows {row_list}) - All retries exhausted")
            failed_chunks.append((start, end))
    
    # Check if all chunks were processed
    if failed_chunks:
        failed_info = "\n  ".join([f"Rows {s}, {', '.join(str(i) for i in range(s, e))}" 
                                   for s, e in failed_chunks])
        raise RuntimeError(f"Failed to compute {len(failed_chunks)} chunk(s):\n  {failed_info}")
    
    # Assemble results in order
    ordered = [results[s] for s in sorted(results.keys())]
    overall_time_ms = (time.time() - overall_start) * 1000
    
    timing_report = {
        'overall_time_ms': overall_time_ms,
        'worker_times': worker_times,
        'handled_by': handled_by,
        'available_workers': available_workers,
        'unavailable_workers': unavailable_workers
    }
    
    return np.vstack(ordered), timing_report
    return np.vstack(ordered), timing_report


def parse_workers_arg(arg):
    if not arg:
        return ["localhost:50051", "localhost:50052", "localhost:50053"]
    return [x.strip() for x in arg.split(",") if x.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Distributed matrix coordinator")
    parser.add_argument("--workers", type=str, default=None, help="Comma-separated worker addresses (host:port)")
    args = parser.parse_args()

    workers = parse_workers_arg(args.workers)

    A, B = get_matrix_from_user()
    A = A.astype(int)
    B = B.astype(int)

    print("\n" + "="*80)
    print("INPUT MATRICES")
    print("="*80)
    print(f"Matrix A (shape {A.shape}):\n{A}")
    print(f"\nMatrix B (shape {B.shape}):\n{B}")
    print("="*80 + "\n")

    C, timing_report = compute_distributed(A, B, workers)

    print("\n" + "="*80)
    print("FINAL RESULT")
    print("="*80)
    print(f"Result matrix C (shape {C.shape}):\n{C.astype(int)}")
    print("="*80 + "\n")

    # Display comprehensive timing report
    print("="*80)
    print("COMPUTATION TIMING REPORT")
    print("="*80)
    
    print(f"\nOverall computation time: {timing_report['overall_time_ms']:.2f} ms")
    print(f"\nWorker Status:")
    print(f"  Available: {len(timing_report['available_workers'])} - {timing_report['available_workers']}")
    if timing_report['unavailable_workers']:
        print(f"  Unavailable: {len(timing_report['unavailable_workers'])} - {timing_report['unavailable_workers']}")
    
    print(f"\nPer-Worker Statistics:")
    print("-" * 80)
    
    for worker_addr in sorted(timing_report['worker_times'].keys()):
        tasks = timing_report['worker_times'][worker_addr]
        total_computation = sum(t['computation_ms'] for t in tasks)
        total_rpc = sum(t['rpc_ms'] for t in tasks)
        avg_computation = total_computation / len(tasks) if tasks else 0
        avg_rpc = total_rpc / len(tasks) if tasks else 0
        
        print(f"\nWorker: {worker_addr}")
        print(f"  Number of task(s): {len(tasks)}")
        for i, task in enumerate(tasks):
            row_list = task.get('row_list', ', '.join(str(r) for r in range(task['rows'][0], task['rows'][1])))
            print(f"    Task {i+1}: Rows {row_list}")
            print(f"      - Computation time: {task['computation_ms']:.2f} ms")
            print(f"      - RPC roundtrip time: {task['rpc_ms']:.2f} ms")
        print(f"  Worker Total:")
        print(f"    - Sum computation: {total_computation:.2f} ms")
        print(f"    - Sum RPC time: {total_rpc:.2f} ms")
        print(f"    - Average computation per task: {avg_computation:.2f} ms")
        print(f"    - Average RPC per task: {avg_rpc:.2f} ms")
    
    print("\n" + "="*80)
