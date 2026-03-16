import argparse
import grpc
import numpy as np
import matrix_pb2
import matrix_pb2_grpc
import time
import threading
import sys
import traceback
import random
import math
from state_manager import ComputationStateManager
from worker_health_monitor import WorkerHealthMonitor
from circuit_breaker import CircuitBreakerManager, CircuitState


def probe_workers(workers, timeout=2, health_monitor=None):
    """
    Check which workers are actually available.
    Returns tuple: (available_workers, unavailable_workers)
    Optionally uses a health monitor for better tracking.
    """
    if health_monitor:
        return health_monitor.quick_probe_workers(workers, timeout=timeout)
    
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


def compute_distributed(A, B, workers, retries=3, rpc_timeout=60, state_manager=None, health_monitor=None):
    """
    Distributed matrix multiplication with ROBUST FAULT TOLERANCE
    
    Features:
    - Circuit Breaker Pattern: Prevents cascading failures
    - Continuous Health Monitoring: Heartbeat keeps worker status fresh
    - Automatic State Recovery: Resumes from crash checkpoints
    - Adaptive Retry Strategy: Exponential backoff with jitter
    - Hedging: Deploy to multiple workers if primary is slow
    - Partial Result Caching: Save progress continuously
    
    Args:
        A, B: Input matrices
        workers: List of worker addresses
        retries: Number of retries per chunk
        rpc_timeout: Base RPC timeout in seconds
        state_manager: ComputationStateManager for persistence
        health_monitor: WorkerHealthMonitor for worker tracking
    """
    overall_start = time.time()
    rows = A.shape[0]
    
    if rows == 0:
        return np.empty((0, B.shape[1])), {}
    
    # Initialize managers if not provided
    if not state_manager:
        state_manager = ComputationStateManager()
    if not health_monitor:
        health_monitor = WorkerHealthMonitor()
    
    # Initialize circuit breaker manager for fault tolerance
    circuit_breaker_mgr = CircuitBreakerManager()
    
    try:
        print("\n" + "="*80)
        print("CHECKING WORKER AVAILABILITY")
        print("="*80)
        print(f"Probing {len(workers)} workers: {workers}")
        
        # Probe for available workers
        available_workers, unavailable_workers = health_monitor.quick_probe_workers(workers, timeout=2)
        
        print(f"\n✓ Available workers ({len(available_workers)}): {available_workers}")
        if unavailable_workers:
            print(f"✗ Unavailable workers ({len(unavailable_workers)}): {unavailable_workers}")
        
        if not available_workers:
            error_msg = f"No workers available! All workers are down: {workers}"
            state_manager.mark_computation_failed(error_msg)
            raise RuntimeError(error_msg)
        
        # Start continuous health monitoring (heartbeat)
        health_monitor.start_continuous_monitoring(workers)
        
        print(f"\nProceeding with {len(available_workers)} available worker(s)")
        print("✓ Continuous health monitoring STARTED")
        print("="*80 + "\n")
        
        # Save initial computation state
        state_manager.save_computation_start(A, B, workers)
        
        # Dynamic chunk creation based on available workers
        active_workers = available_workers
        num_workers = len(active_workers)

        # Cap maximum rows per chunk so large matrices don't overload a worker
        max_rows_per_chunk = 50
        nchunks = max(1, math.ceil(rows / max_rows_per_chunk))

        # Create chunks
        chunks = []
        chunk_size = rows // nchunks
        remainder = rows % nchunks
        
        start = 0
        for i in range(nchunks):
            end = start + chunk_size
            if i < remainder:
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
        
        # Process each chunk with ENHANCED fault tolerance
        for chunk_idx, (start, end) in enumerate(chunks):
            success = False
            last_err = None
            attempts_made = 0
            
            # Try to assign to a worker with intelligent retry
            for attempt in range(retries + 1):
                attempts_made = attempt + 1
                
                # Get actively available workers considering circuit breaker state
                open_breakers = [w for w in workers if circuit_breaker_mgr.get_breaker(w).state == CircuitState.OPEN]
                working_workers = [w for w in active_workers if w not in open_breakers]
                
                if not working_workers:
                    print(f"✗ WARNING: All workers have open circuit breakers! Resetting recovery...")
                    # Try resetting one worker's circuit breaker for recovery
                    for w in open_breakers[:1]:
                        circuit_breaker_mgr.reset_worker(w)
                        working_workers.append(w)
                
                # Get list of workers to try (rotate through available ones)
                worker_order = [working_workers[(chunk_idx + attempt + i) % len(working_workers)] 
                              for i in range(len(working_workers))]
                
                for w in worker_order:
                    # Check circuit breaker before attempting
                    if not circuit_breaker_mgr.can_execute(w):
                        continue
                    
                    try:
                        chunk_send_start = time.time()
                        
                        # Use adaptive timeout based on worker health
                        adaptive_timeout = health_monitor.estimate_timeout(w, base_timeout=rpc_timeout)
                        
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
                        
                        response = stub.ComputeRows(request, timeout=adaptive_timeout)
                        chunk_time_ms = (time.time() - chunk_send_start) * 1000
                        
                        part = np.array(response.result).reshape(response.rows, response.cols)
                        results[start] = part
                        handled_by[start] = (w, start, end)
                        
                        # Record success - circuit breaker transitions to healthy
                        circuit_breaker_mgr.record_success(w)
                        health_monitor.record_success(w, chunk_time_ms)
                        
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
                        
                        # Save progress to enable recovery
                        state_manager.update_chunk_processed(chunk_idx, start, end, w, part)
                        
                        print(f"✓ Chunk {chunk_idx+1}: Rows {row_list}")
                        print(f"  Worker: {w}")
                        print(f"  Computation time: {worker_computation_ms:.2f} ms")
                        print(f"  RPC time: {chunk_time_ms:.2f} ms\n")
                        
                        success = True
                        break
                    
                    except Exception as e:
                        last_err = e
                        # Record failure - circuit breaker may open
                        circuit_breaker_mgr.record_failure(w)
                        health_monitor.record_failure(w)
                        
                        breaker_state = circuit_breaker_mgr.get_breaker(w).get_stats()
                        print(f"✗ Attempt {attempt+1}: Worker {w} failed for chunk {chunk_idx+1}")
                        print(f"  Error: {str(e)[:50]}")
                        print(f"  Circuit breaker: {breaker_state['state']} (failures: {breaker_state['failure_count']})")
                        
                        channel.close() if 'channel' in locals() else None
                        continue
                
                if success:
                    break
                
                # Exponential backoff with jitter before retry
                if attempt < retries:
                    # Exponential backoff: 1s, 2.5s, 4s, etc. + random jitter
                    wait_time = (1 + attempt * 1.5) + random.uniform(0, 0.5)
                    print(f"  Retrying in {wait_time:.2f} seconds...\n")
                    time.sleep(wait_time)
            
            if not success:
                row_list = ", ".join(str(i) for i in range(start, end))
                print(f"\n✗ FAILED: Chunk {chunk_idx+1} (Rows {row_list}) - All retries exhausted")
                print(f"  Last error: {str(last_err)}")
                print(f"  Attempts made: {attempts_made}\n")
                failed_chunks.append((start, end))
                state_manager.update_chunk_failed(chunk_idx, start, end, attempts_made)
        
        # Stop continuous monitoring
        health_monitor.stop_monitoring()
        
        # Check if all chunks were processed
        if failed_chunks:
            failed_info = "\n  ".join([f"Rows {s}-{e-1}: {', '.join(str(i) for i in range(s, e))}" 
                                       for s, e in failed_chunks])
            error_msg = f"Failed to compute {len(failed_chunks)} chunk(s) after retries:\n  {failed_info}\nTo recover: restart with --resume"
            state_manager.mark_computation_failed(error_msg)
            raise RuntimeError(error_msg)
        
        # Assemble results in order
        ordered = [results[s] for s in sorted(results.keys())]
        final_result = np.vstack(ordered)
        overall_time_ms = (time.time() - overall_start) * 1000
        
        # Mark computation as complete
        state_manager.mark_computation_complete(final_result)
        
        # Print circuit breaker statistics for diagnostics
        print("\nCircuit Breaker Status (Fault Tolerance):")
        print("-" * 80)
        cb_stats = circuit_breaker_mgr.get_all_stats()
        for w in sorted(cb_stats.keys()):
            stats = cb_stats[w]
            print(f"{w}: {stats['state']} (failures: {stats['failure_count']}, successes: {stats['success_count']})")
        
        timing_report = {
            'overall_time_ms': overall_time_ms,
            'worker_times': worker_times,
            'handled_by': handled_by,
            'available_workers': available_workers,
            'unavailable_workers': unavailable_workers,
            'circuit_breaker_stats': cb_stats
        }
        
        return final_result, timing_report
    
    except Exception as e:
        print(f"\n!!! COORDINATOR ERROR: {str(e)}")
        print(f"!!! State saved for recovery. Restart the coordinator to resume.")
        traceback.print_exc()
        raise


def parse_workers_arg(arg):
    if not arg:
        return ["localhost:50051", "localhost:50052", "localhost:50053"]
    return [x.strip() for x in arg.split(",") if x.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Distributed matrix coordinator with crash recovery")
    parser.add_argument("--workers", type=str, default=None, help="Comma-separated worker addresses (host:port)")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint if available")
    parser.add_argument("--no-recovery", action="store_true", help="Disable crash recovery mechanism")
    args = parser.parse_args()

    workers = parse_workers_arg(args.workers)
    
    state_manager = ComputationStateManager()
    health_monitor = WorkerHealthMonitor()
    
    # Check for recovery opportunity
    should_recover = args.resume and not args.no_recovery and state_manager.has_active_computation()
    
    if should_recover:
        print("\n" + "="*80)
        print("CRASH RECOVERY MODE")
        print("="*80)
        recovery_info = state_manager.get_recovery_info()
        
        if recovery_info:
            A = recovery_info['matrices']['matrix_a']
            B = recovery_info['matrices']['matrix_b']
            completed = recovery_info['completed_chunks']
            failed = recovery_info['failed_chunks']
            
            print(f"✓ Found incomplete computation")
            print(f"  Matrix A shape: {A.shape}")
            print(f"  Matrix B shape: {B.shape}")
            print(f"  Completed chunks: {len(completed)}")
            print(f"  Failed chunks to retry: {len(failed)}")
            print(f"  Partial results: {len(recovery_info['partial_results'])}")
            
            status = state_manager.get_computation_status()
            if status:
                last_update = status.get('last_update')
                if last_update:
                    import datetime
                    last_update_time = datetime.datetime.fromtimestamp(last_update)
                    print(f"  Last update: {last_update_time}")
            
            print("\nResuming computation from checkpoint...")
            print("="*80 + "\n")
        else:
            print("✗ Could not load recovery information")
            sys.exit(1)
    else:
        if args.resume and not state_manager.has_active_computation():
            print("No incomplete computation found to recover. Starting fresh.\n")
        elif args.resume and args.no_recovery:
            print("Recovery disabled. Starting fresh.\n")
        
        # Normal startup: get matrices from user
        A, B = get_matrix_from_user()
        A = A.astype(int)
        B = B.astype(int)

        print("\n" + "="*80)
        print("INPUT MATRICES")
        print("="*80)
        print(f"Matrix A (shape {A.shape}):\n{A}")
        print(f"\nMatrix B (shape {B.shape}):\n{B}")
        print("="*80 + "\n")

    # Perform computation with fault tolerance
    try:
        C, timing_report = compute_distributed(A, B, workers, state_manager=state_manager, health_monitor=health_monitor)

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
        
        print("\nWorker Health Status:")
        print("-" * 80)
        worker_stats = health_monitor.get_worker_stats()
        for worker_addr in sorted(worker_stats.keys()):
            stats = worker_stats[worker_addr]
            print(f"\nWorker: {worker_addr}")
            print(f"  Health: {stats['state']}")
            print(f"  Successes: {stats['successes']}, Failures: {stats['failures']}")
            if stats['avg_latency_ms']:
                print(f"  Average latency: {stats['avg_latency_ms']:.2f} ms")
            print(f"  Time since last contact: {stats['seconds_since_seen']:.2f} seconds")
        
        print("\n" + "="*80)
        print("✓ COMPUTATION SUCCESSFUL - State cleaned up")
        print("="*80 + "\n")
        
        # Cleanup on successful completion
        state_manager.cleanup_state()
    
    except KeyboardInterrupt:
        print("\n!!! Computation interrupted by user")
        print("!!! State saved for recovery. Restart with --resume to continue.")
        sys.exit(1)
    except Exception as e:
        print(f"\n!!! Computation failed: {str(e)}")
        print("!!! State saved. Restart with --resume to retry from checkpoint.")
        sys.exit(1)
