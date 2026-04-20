#!/usr/bin/env python3
"""
distributed_state.py - Distributed State Management

Manages distributed computation state with versioning, consistency guarantees,
and conflict resolution. Uses vector clocks for causality tracking.
"""

import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from threading import RLock
from dataclasses import dataclass, field, asdict
from collections import defaultdict


logger = logging.getLogger(__name__)


@dataclass
class ChunkState:
    """State of a computation chunk."""
    chunk_id: int
    status: str
    assigned_to: Optional[str] = None
    result: Optional[List[float]] = None
    vector_clock: Dict[str, int] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    retries: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class StateVersion:
    """Versioned state snapshot."""
    version: int
    timestamp: float
    chunks: Dict[int, ChunkState]
    vector_clock: Dict[str, int]


class DistributedState:
    """Manages state of distributed matrix computation."""
    
    def __init__(self, node_id: str, num_chunks: int):
        """Initialize distributed state."""
        self._node_id = node_id
        self._num_chunks = num_chunks
        self._chunks: Dict[int, ChunkState] = {}
        self._vector_clock: Dict[str, int] = defaultdict(int)
        self._vector_clock[node_id] = 0  # Initialize own clock
        self._version = 0
        self._versions: List[StateVersion] = []
        self._lock = RLock()
        
        for i in range(num_chunks):
            self._chunks[i] = ChunkState(chunk_id=i, status="pending")
    
    def _increment_clock(self) -> None:
        """Increment this node's logical clock."""
        self._vector_clock[self._node_id] += 1
    
    def update_chunk(self, chunk_id: int, status: str, 
                    assigned_to: Optional[str] = None,
                    result: Optional[List[float]] = None) -> None:
        """Update chunk state."""
        with self._lock:
            if chunk_id >= self._num_chunks or chunk_id < 0:
                raise ValueError(f"Invalid chunk_id: {chunk_id}")
            
            self._increment_clock()
            
            chunk = self._chunks[chunk_id]
            chunk.status = status
            if assigned_to:
                chunk.assigned_to = assigned_to
            if result is not None:
                chunk.result = result
            chunk.vector_clock = dict(self._vector_clock)
            chunk.timestamp = time.time()
            
            logger.debug(f"Updated chunk {chunk_id}: status={status}")
    
    def merge_update(self, chunk_id: int, remote_state: Dict[str, Any],
                     remote_clock: Dict[str, int]) -> bool:
        """Merge a remote chunk state update (gossip)."""
        with self._lock:
            if chunk_id >= self._num_chunks or chunk_id < 0:
                return False
            
            local_chunk = self._chunks[chunk_id]
            local_clock = local_chunk.vector_clock
            
            is_later = self._is_causally_later(remote_clock, local_clock)
            
            if not is_later:
                return False
            
            local_chunk.status = remote_state.get("status", local_chunk.status)
            if "assigned_to" in remote_state:
                local_chunk.assigned_to = remote_state["assigned_to"]
            if "result" in remote_state and remote_state["result"]:
                local_chunk.result = remote_state["result"]
            local_chunk.vector_clock = dict(remote_clock)
            local_chunk.timestamp = time.time()
            
            logger.debug(f"Merged remote update for chunk {chunk_id}")
            return True
    
    def _is_causally_later(self, clock_a: Dict[str, int], 
                          clock_b: Dict[str, int]) -> bool:
        """Check if clock_a happened after clock_b causally."""
        greater_or_equal = all(
            clock_a.get(node, 0) >= clock_b.get(node, 0)
            for node in set(list(clock_a.keys()) + list(clock_b.keys()))
        )
        
        strictly_greater = any(
            clock_a.get(node, 0) > clock_b.get(node, 0)
            for node in set(list(clock_a.keys()) + list(clock_b.keys()))
        )
        
        return greater_or_equal and strictly_greater
    
    def get_chunk(self, chunk_id: int) -> Optional[ChunkState]:
        """Get chunk state."""
        with self._lock:
            if chunk_id >= self._num_chunks:
                return None
            return self._chunks[chunk_id]
    
    def get_all_chunks(self) -> Dict[int, ChunkState]:
        """Get all chunk states."""
        with self._lock:
            return dict(self._chunks)
    
    def get_vector_clock(self) -> Dict[str, int]:
        """Get current vector clock."""
        with self._lock:
            return dict(self._vector_clock)
    
    def get_completion_stats(self) -> Dict[str, Any]:
        """Get computation completion statistics."""
        with self._lock:
            completed = sum(1 for c in self._chunks.values()
                           if c.status == "completed")
            failed = sum(1 for c in self._chunks.values()
                        if c.status == "failed")
            
            return {
                "total_chunks": self._num_chunks,
                "completed": completed,
                "failed": failed,
                "pending": self._num_chunks - completed - failed,
                "completion_percent": (completed / self._num_chunks * 100) if self._num_chunks > 0 else 0,
            }


__all__ = ['DistributedState', 'ChunkState', 'StateVersion']
