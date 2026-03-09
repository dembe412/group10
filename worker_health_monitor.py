"""
Worker Health Monitor with Continuous Heartbeat
Tracks worker availability and provides intelligent failure detection
"""
import grpc
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from circuit_breaker import CircuitBreakerManager


class WorkerHealthMonitor:
    """Monitors health of remote workers with adaptive timeout strategies"""
    
    def __init__(self, health_check_interval=5):
        self.health_check_interval = health_check_interval
        self.worker_status = {}  # worker_addr -> {'last_seen': time, 'state': 'healthy'|'degraded'|'unhealthy', 'failures': int}
        self.worker_latencies = defaultdict(list)  # worker_addr -> [latency_ms, ...]
        self.lock = threading.Lock()
        self.monitoring = False
    
    def register_worker(self, worker_addr):
        """Register a worker for monitoring"""
        with self.lock:
            if worker_addr not in self.worker_status:
                self.worker_status[worker_addr] = {
                    'last_seen': time.time(),
                    'state': 'unknown',
                    'failures': 0,
                    'successes': 0,
                    'registered_at': time.time()
                }
    
    def record_success(self, worker_addr, latency_ms):
        """Record successful communication with worker"""
        with self.lock:
            if worker_addr not in self.worker_status:
                self.register_worker(worker_addr)
            
            self.worker_status[worker_addr]['last_seen'] = time.time()
            self.worker_status[worker_addr]['failures'] = 0
            self.worker_status[worker_addr]['successes'] += 1
            self.worker_status[worker_addr]['state'] = 'healthy'
            
            self.worker_latencies[worker_addr].append(latency_ms)
            # Keep only recent latencies (last 100)
            if len(self.worker_latencies[worker_addr]) > 100:
                self.worker_latencies[worker_addr].pop(0)
    
    def record_failure(self, worker_addr):
        """Record failed communication with worker"""
        with self.lock:
            if worker_addr not in self.worker_status:
                self.register_worker(worker_addr)
            
            self.worker_status[worker_addr]['failures'] += 1
            self.worker_status[worker_addr]['last_seen'] = time.time()
            
            failures = self.worker_status[worker_addr]['failures']
            if failures >= 5:
                self.worker_status[worker_addr]['state'] = 'unhealthy'
            elif failures >= 2:
                self.worker_status[worker_addr]['state'] = 'degraded'
            else:
                self.worker_status[worker_addr]['state'] = 'healthy'
    
    def get_worker_state(self, worker_addr):
        """Get current state of a worker"""
        with self.lock:
            if worker_addr not in self.worker_status:
                return 'unknown'
            return self.worker_status[worker_addr]['state']
    
    def get_healthy_workers(self, workers):
        """Get subset of workers that are currently healthy"""
        with self.lock:
            return [w for w in workers if self.worker_status.get(w, {}).get('state') != 'unhealthy']
    
    def get_worker_stats(self):
        """Get comprehensive worker statistics"""
        with self.lock:
            stats = {}
            for worker, status in self.worker_status.items():
                avg_latency = None
                if worker in self.worker_latencies and self.worker_latencies[worker]:
                    avg_latency = sum(self.worker_latencies[worker]) / len(self.worker_latencies[worker])
                
                stats[worker] = {
                    'state': status['state'],
                    'failures': status['failures'],
                    'successes': status['successes'],
                    'avg_latency_ms': avg_latency,
                    'seconds_since_seen': time.time() - status['last_seen']
                }
            return stats
    
    def estimate_timeout(self, worker_addr, base_timeout=10):
        """
        Estimate appropriate timeout for a worker based on historical latency.
        Longer timeouts for slower workers prevent false negatives.
        """
        with self.lock:
            if worker_addr not in self.worker_latencies or not self.worker_latencies[worker_addr]:
                return base_timeout
            
            avg_latency = sum(self.worker_latencies[worker_addr]) / len(self.worker_latencies[worker_addr])
            # Use average + 2x standard deviation as timeout
            latencies = self.worker_latencies[worker_addr]
            if len(latencies) > 1:
                variance = sum((x - avg_latency) ** 2 for x in latencies) / len(latencies)
                std_dev = variance ** 0.5
                estimated_timeout = (avg_latency + 2 * std_dev) / 1000 + 2  # +2 second buffer
                return min(max(estimated_timeout, 2), base_timeout)  # Between 2s and base_timeout
            else:
                return min(max(avg_latency / 1000 + 2, 2), base_timeout)
    
    def is_worker_available(self, worker_addr, timeout=2):
        """Check if worker is currently available with probe"""
        try:
            channel = grpc.insecure_channel(worker_addr)
            grpc.channel_ready_future(channel).result(timeout=timeout)
            channel.close()
            self.record_success(worker_addr, 0)
            return True
        except Exception as e:
            self.record_failure(worker_addr)
            return False
    
    def quick_probe_workers(self, workers, timeout=2):
        """
        Quickly probe multiple workers in parallel
        Returns tuple: (available_workers, unavailable_workers)
        """
        available = []
        unavailable = []
        
        def check_worker(w):
            start = time.time()
            try:
                channel = grpc.insecure_channel(w)
                grpc.channel_ready_future(channel).result(timeout=timeout)
                channel.close()
                latency_ms = (time.time() - start) * 1000
                self.record_success(w, latency_ms)
                available.append(w)
            except Exception:
                self.record_failure(w)
                unavailable.append(w)
        
        threads = []
        for w in workers:
            self.register_worker(w)
            t = threading.Thread(target=check_worker, args=(w,))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        return available, unavailable
    
    def start_continuous_monitoring(self, workers):
        """
        Start background thread for continuous worker health checks (heartbeat)
        Detects failures faster and updates worker states continuously
        """
        if self.monitoring:
            return
        
        self.monitoring = True
        monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(workers,),
            daemon=True
        )
        monitor_thread.start()
    
    def _monitor_loop(self, workers):
        """Background monitoring loop - runs continuously"""
        while self.monitoring:
            try:
                # Heartbeat check for each worker
                for worker in workers:
                    try:
                        if self.is_worker_available(worker, timeout=1):
                            pass  # Update recorded
                    except Exception:
                        pass
                
                # Sleep before next check
                time.sleep(self.health_check_interval)
            except Exception:
                # Log error but don't crash monitor
                time.sleep(self.health_check_interval)
    
    def stop_monitoring(self):
        """Stop continuous monitoring"""
        self.monitoring = False
    
    def get_monitor_status(self):
        """Get monitoring status"""
        return {
            'monitoring': self.monitoring,
            'worker_count': len(self.worker_status),
            'worker_stats': self.get_worker_stats()
        }

        for w in workers:
            self.register_worker(w)
            t = threading.Thread(target=check_worker, args=(w,))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        return available, unavailable
