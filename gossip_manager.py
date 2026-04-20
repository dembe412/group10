#!/usr/bin/env python3
"""
gossip_manager.py - Gossip Protocol Implementation

Implements epidemic gossip protocol for state propagation across the
P2P network. Ensures eventual consistency with minimal overhead.
"""

import logging
import time
from typing import Dict, List, Optional, Set
from threading import RLock
from collections import deque
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class GossipMessage:
    """A single gossip message."""
    message_id: str
    node_id: str
    timestamp: float = field(default_factory=time.time)
    payload: Dict = field(default_factory=dict)
    seen_by: Set[str] = field(default_factory=set)
    hops: int = 0


class GossipManager:
    """Manages gossip protocol for state synchronization."""
    
    def __init__(self, node_id: str, max_fanout: int = 3,
                 message_ttl: int = 60):
        """Initialize gossip manager."""
        self._node_id = node_id
        self._max_fanout = max_fanout
        self._message_ttl = message_ttl
        self._messages: Dict[str, GossipMessage] = {}
        self._sent_messages: Set[str] = set()
        self._queue: deque = deque(maxlen=1000)
        self._lock = RLock()
    
    def add_message(self, message_id: str, payload: Dict,
                   from_node: Optional[str] = None) -> None:
        """Add a gossip message."""
        with self._lock:
            if message_id in self._messages:
                return
            
            msg = GossipMessage(
                message_id=message_id,
                node_id=from_node or self._node_id,
                payload=payload
            )
            self._messages[message_id] = msg
            self._queue.append(message_id)
            
            logger.debug(f"Added gossip message {message_id} from {msg.node_id}")
    
    def get_messages_to_send(self, fanout: Optional[int] = None) -> List[str]:
        """Get messages ready to gossip."""
        if fanout is None:
            fanout = self._max_fanout
        
        with self._lock:
            messages_to_send = []
            
            for msg_id in list(self._messages.keys()):
                msg = self._messages[msg_id]
                
                if time.time() - msg.timestamp > self._message_ttl:
                    del self._messages[msg_id]
                    continue
                
                if msg_id not in self._sent_messages:
                    messages_to_send.append(msg_id)
                    if len(messages_to_send) >= fanout:
                        break
            
            return messages_to_send
    
    def mark_sent(self, message_id: str, target_nodes: List[str]) -> None:
        """Mark a message as sent to target nodes."""
        with self._lock:
            if message_id in self._messages:
                msg = self._messages[message_id]
                msg.seen_by.update(target_nodes)
                self._sent_messages.add(message_id)
    
    def cleanup_old_messages(self) -> int:
        """Remove expired messages."""
        with self._lock:
            expired = [mid for mid, msg in self._messages.items()
                      if time.time() - msg.timestamp > self._message_ttl]
            
            for mid in expired:
                del self._messages[mid]
            
            if expired:
                logger.debug(f"Cleaned up {len(expired)} expired gossip messages")
            
            return len(expired)
    
    def get_stats(self) -> Dict[str, int]:
        """Get gossip statistics."""
        with self._lock:
            return {
                "messages_in_flight": len(self._messages),
                "messages_sent": len(self._sent_messages),
                "queue_size": len(self._queue),
            }


__all__ = ['GossipManager', 'GossipMessage']
