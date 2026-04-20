#!/usr/bin/env python3
"""
peer_health.py - Peer Health Monitoring and Failure Detection

Monitors health of peers using active probing and passive observations.
"""

import logging
import time
from typing import Dict, List, Optional
from threading import RLock
from enum import Enum
from dataclasses import dataclass


logger = logging.getLogger(__name__)


class PeerHealth(Enum):
    """Health status of a peer."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthMetrics:
    """Health metrics for a peer."""
    node_id: str
    status: PeerHealth = PeerHealth.UNKNOWN
    last_probe: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    reputation: float = 1.0
    response_time_ms: float = 0.0
    consecutive_failures: int = 0
    backoff_until: float = 0.0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total
    
    def is_circuit_broken(self) -> bool:
        """Check if circuit breaker is active."""
        return time.time() < self.backoff_until


class PeerHealthMonitor:
    """Monitors and tracks health of peers in the network."""
    
    def __init__(self, failure_threshold: int = 3, 
                 backoff_multiplier: float = 2.0,
                 initial_backoff: float = 1.0):
        """Initialize health monitor."""
        self._metrics: Dict[str, HealthMetrics] = {}
        self._failure_threshold = failure_threshold
        self._backoff_multiplier = backoff_multiplier
        self._initial_backoff = initial_backoff
        self._lock = RLock()
    
    def record_success(self, node_id: str, response_time_ms: float = 0.0) -> None:
        """Record a successful probe/interaction."""
        with self._lock:
            if node_id not in self._metrics:
                self._metrics[node_id] = HealthMetrics(node_id=node_id)
            
            metrics = self._metrics[node_id]
            metrics.success_count += 1
            metrics.last_probe = time.time()
            metrics.response_time_ms = response_time_ms
            metrics.consecutive_failures = 0
            metrics.reputation = min(1.0, metrics.reputation + 0.1)
            metrics.backoff_until = 0.0
            
            if metrics.success_rate > 0.9:
                metrics.status = PeerHealth.HEALTHY
            else:
                metrics.status = PeerHealth.DEGRADED
            
            logger.debug(f"Peer {node_id} health: success (rate={metrics.success_rate:.2%})")
    
    def record_failure(self, node_id: str) -> None:
        """Record a failed probe/interaction."""
        with self._lock:
            if node_id not in self._metrics:
                self._metrics[node_id] = HealthMetrics(node_id=node_id)
            
            metrics = self._metrics[node_id]
            metrics.failure_count += 1
            metrics.last_probe = time.time()
            metrics.consecutive_failures += 1
            metrics.reputation = max(0.0, metrics.reputation - 0.2)
            
            if metrics.consecutive_failures >= self._failure_threshold:
                backoff_duration = (
                    self._initial_backoff * 
                    (self._backoff_multiplier ** 
                     (metrics.consecutive_failures - self._failure_threshold))
                )
                metrics.backoff_until = time.time() + backoff_duration
                metrics.status = PeerHealth.UNHEALTHY
                logger.warning(
                    f"Peer {node_id} unhealthy, circuit breaker activated "
                    f"for {backoff_duration:.1f}s"
                )
            else:
                metrics.status = PeerHealth.DEGRADED
                logger.warning(f"Peer {node_id} failure (count={metrics.failure_count})")
    
    def get_status(self, node_id: str) -> PeerHealth:
        """Get current health status of a peer."""
        with self._lock:
            if node_id not in self._metrics:
                return PeerHealth.UNKNOWN
            
            metrics = self._metrics[node_id]
            
            if (metrics.status == PeerHealth.UNHEALTHY and 
                not metrics.is_circuit_broken()):
                metrics.consecutive_failures = 0
                metrics.status = PeerHealth.DEGRADED
            
            return metrics.status
    
    def get_healthy_peers(self) -> List[str]:
        """Get list of healthy peers."""
        with self._lock:
            healthy = []
            for node_id, metrics in self._metrics.items():
                if (metrics.status == PeerHealth.UNHEALTHY and
                    not metrics.is_circuit_broken()):
                    metrics.consecutive_failures = 0
                    metrics.status = PeerHealth.DEGRADED
                
                if metrics.status == PeerHealth.HEALTHY:
                    healthy.append(node_id)
            
            return healthy
    
    def should_probe(self, node_id: str) -> bool:
        """Check if we should probe this peer."""
        with self._lock:
            if node_id not in self._metrics:
                return True
            
            metrics = self._metrics[node_id]
            return not metrics.is_circuit_broken()


__all__ = ['PeerHealthMonitor', 'PeerHealth', 'HealthMetrics']
