"""
Circuit Breaker Pattern for Fault Tolerance
Prevents cascading failures by stopping calls to failing services
"""
import time
from datetime import datetime, timedelta
from threading import Lock
from enum import Enum


class CircuitState(Enum):
    """Circle breaker states"""
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Rejecting calls
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """
    Circuit breaker implementation for worker fault tolerance
    
    Triple-state pattern:
    - CLOSED: Normal operation, calls go through
    - OPEN: Failure threshold exceeded, calls rejected
    - HALF_OPEN: Testing if service recovered
    """
    
    def __init__(self, failure_threshold=3, recovery_timeout=30, success_threshold=2):
        """
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before transitioning to HALF_OPEN
            success_threshold: Consecutive successes needed to close circuit
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.last_state_change = datetime.now()
        self.lock = Lock()
    
    def can_execute(self):
        """Check if request can be executed"""
        with self.lock:
            if self.state == CircuitState.CLOSED:
                return True
            elif self.state == CircuitState.OPEN:
                # Check if recovery timeout elapsed
                if self._should_attempt_reset():
                    self._transition_to_half_open()
                    return True
                return False
            else:  # HALF_OPEN
                return True
    
    def record_success(self):
        """Record successful call"""
        with self.lock:
            self.failure_count = 0
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self._transition_to_closed()
            elif self.state == CircuitState.CLOSED:
                self.success_count = min(self.success_count + 1, self.success_threshold)
    
    def record_failure(self):
        """Record failed call"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            self.success_count = 0
            
            if self.state == CircuitState.HALF_OPEN:
                # Failed during recovery attempt - reopen
                self._transition_to_open()
            elif self.state == CircuitState.CLOSED and self.failure_count >= self.failure_threshold:
                # Threshold exceeded - open circuit
                self._transition_to_open()
    
    def _should_attempt_reset(self):
        """Check if recovery timeout has elapsed"""
        if self.last_failure_time is None:
            return False
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout
    
    def _transition_to_open(self):
        """Transition circuit to OPEN state"""
        self.state = CircuitState.OPEN
        self.last_state_change = datetime.now()
        self.success_count = 0
    
    def _transition_to_half_open(self):
        """Transition circuit to HALF_OPEN state"""
        self.state = CircuitState.HALF_OPEN
        self.last_state_change = datetime.now()
        self.success_count = 0
        self.failure_count = 0
    
    def _transition_to_closed(self):
        """Transition circuit to CLOSED state"""
        self.state = CircuitState.CLOSED
        self.last_state_change = datetime.now()
        self.failure_count = 0
        self.success_count = 0
    
    def get_state(self):
        """Get current circuit state"""
        with self.lock:
            return self.state
    
    def get_stats(self):
        """Get circuit breaker statistics"""
        with self.lock:
            return {
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None,
                'seconds_since_state_change': (datetime.now() - self.last_state_change).total_seconds()
            }


class CircuitBreakerManager:
    """Manages circuit breakers for multiple workers"""
    
    def __init__(self):
        """Initialize manager"""
        self.breakers = {}
        self.lock = Lock()
    
    def get_breaker(self, worker_addr):
        """Get or create circuit breaker for worker"""
        with self.lock:
            if worker_addr not in self.breakers:
                self.breakers[worker_addr] = CircuitBreaker()
            return self.breakers[worker_addr]
    
    def can_execute(self, worker_addr):
        """Check if worker can execute"""
        return self.get_breaker(worker_addr).can_execute()
    
    def record_success(self, worker_addr):
        """Record success for worker"""
        self.get_breaker(worker_addr).record_success()
    
    def record_failure(self, worker_addr):
        """Record failure for worker"""
        self.get_breaker(worker_addr).record_failure()
    
    def get_available_workers(self, worker_list):
        """Get workers whose circuit is not OPEN"""
        available = []
        for w in worker_list:
            breaker = self.get_breaker(w)
            if breaker.can_execute() and breaker.state != CircuitState.OPEN:
                available.append(w)
        return available
    
    def get_all_stats(self):
        """Get stats for all workers"""
        with self.lock:
            return {w: b.get_stats() for w, b in self.breakers.items()}
    
    def reset_worker(self, worker_addr):
        """Force reset a worker's circuit breaker"""
        with self.lock:
            if worker_addr in self.breakers:
                self.breakers[worker_addr] = CircuitBreaker()
