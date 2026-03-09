"""
State Manager for Persistent Coordinator State
Enables coordinator recovery from crashes with zero data loss
"""
import json
import os
import time
from pathlib import Path
import pickle
import numpy as np


class ComputationStateManager:
    """Manages persistent state for matrix multiplication computations"""
    
    def __init__(self, state_dir="./computation_state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(exist_ok=True)
        self.active_file = self.state_dir / "active_computation.json"
        self.checkpoint_file = self.state_dir / "checkpoint.pkl"
        self.results_file = self.state_dir / "partial_results.pkl"
    
    def save_computation_start(self, matrix_a, matrix_b, workers):
        """Save initial computation state at startup"""
        state = {
            'timestamp': time.time(),
            'status': 'in_progress',
            'workers_requested': workers,
            'matrix_a_shape': matrix_a.shape,
            'matrix_b_shape': matrix_b.shape,
            'completed_chunks': [],
            'failed_chunks': [],
        }
        
        # Save matrices to disk
        with open(self.checkpoint_file, 'wb') as f:
            pickle.dump({
                'matrix_a': matrix_a,
                'matrix_b': matrix_b,
            }, f)
        
        # Save state metadata
        self._save_json_state(state)
        return state
    
    def _save_json_state(self, state):
        """Save JSON state (excluding arrays)"""
        json_safe_state = {
            k: v for k, v in state.items() 
            if k not in ['matrix_a_shape', 'matrix_b_shape'] or isinstance(v, (int, str, list, dict))
        }
        json_safe_state['matrix_a_shape'] = list(state['matrix_a_shape'])
        json_safe_state['matrix_b_shape'] = list(state['matrix_b_shape'])
        
        with open(self.active_file, 'w') as f:
            json.dump(json_safe_state, f, indent=2)
    
    def update_chunk_processed(self, chunk_id, start, end, worker, result_array):
        """Update state when a chunk is successfully processed"""
        if os.path.exists(self.active_file):
            with open(self.active_file, 'r') as f:
                state = json.load(f)
        else:
            state = {}
        
        state['last_update'] = time.time()
        state['status'] = 'in_progress'
        
        if 'completed_chunks' not in state:
            state['completed_chunks'] = []
        
        state['completed_chunks'].append({
            'chunk_id': chunk_id,
            'rows': [start, end],
            'completed_by': worker,
            'timestamp': time.time()
        })
        
        self._save_json_state(state)
        
        # Append result to partial results
        self._save_partial_result(start, result_array)
    
    def update_chunk_failed(self, chunk_id, start, end, attempts):
        """Update state when a chunk fails"""
        if os.path.exists(self.active_file):
            with open(self.active_file, 'r') as f:
                state = json.load(f)
        else:
            state = {}
        
        state['last_update'] = time.time()
        
        if 'failed_chunks' not in state:
            state['failed_chunks'] = []
        
        state['failed_chunks'].append({
            'chunk_id': chunk_id,
            'rows': [start, end],
            'attempts': attempts,
            'timestamp': time.time()
        })
        
        self._save_json_state(state)
    
    def _save_partial_result(self, start_row, result_array):
        """Save partial result to disk"""
        results = {}
        if os.path.exists(self.results_file):
            with open(self.results_file, 'rb') as f:
                results = pickle.load(f)
        
        results[start_row] = result_array
        
        with open(self.results_file, 'wb') as f:
            pickle.dump(results, f)
    
    def get_partial_results(self):
        """Retrieve partial results from disk"""
        if os.path.exists(self.results_file):
            with open(self.results_file, 'rb') as f:
                return pickle.load(f)
        return {}
    
    def get_checkpoint(self):
        """Load checkpoint (matrices and metadata)"""
        if os.path.exists(self.checkpoint_file) and os.path.exists(self.active_file):
            with open(self.checkpoint_file, 'rb') as f:
                matrices = pickle.load(f)
            with open(self.active_file, 'r') as f:
                state = json.load(f)
            return matrices, state
        return None, None
    
    def has_active_computation(self):
        """Check if there's an incomplete computation to recover"""
        if not os.path.exists(self.active_file):
            return False
        
        try:
            with open(self.active_file, 'r') as f:
                state = json.load(f)
            
            # Check if status is in_progress
            return state.get('status') == 'in_progress'
        except:
            return False
    
    def mark_computation_complete(self, final_result):
        """Mark computation as complete and save final result"""
        if os.path.exists(self.active_file):
            with open(self.active_file, 'r') as f:
                state = json.load(f)
        else:
            state = {}
        
        state['status'] = 'completed'
        state['completion_time'] = time.time()
        
        self._save_json_state(state)
        
        # Save final result
        result_file = self.state_dir / "final_result.pkl"
        with open(result_file, 'wb') as f:
            pickle.dump(final_result, f)
    
    def mark_computation_failed(self, error_message):
        """Mark computation as failed"""
        if os.path.exists(self.active_file):
            with open(self.active_file, 'r') as f:
                state = json.load(f)
        else:
            state = {}
        
        state['status'] = 'failed'
        state['error'] = error_message
        state['failure_time'] = time.time()
        
        self._save_json_state(state)
    
    def get_computation_status(self):
        """Get current computation status"""
        if os.path.exists(self.active_file):
            with open(self.active_file, 'r') as f:
                return json.load(f)
        return None
    
    def cleanup_state(self):
        """Clean up state files after successful completion"""
        for f in [self.active_file, self.checkpoint_file, self.results_file]:
            if os.path.exists(f):
                os.remove(f)
    
    def get_recovery_info(self):
        """Get info needed to recover from crash"""
        matrices, state = self.get_checkpoint()
        partial_results = self.get_partial_results()
        
        if matrices is None:
            return None
        
        # Determine which chunks still need processing
        if state:
            completed = {chunk['chunk_id'] for chunk in state.get('completed_chunks', [])}
            failed = {chunk['chunk_id'] for chunk in state.get('failed_chunks', [])}
        else:
            completed = set()
            failed = set()
        
        return {
            'matrices': matrices,
            'state': state,
            'partial_results': partial_results,
            'completed_chunks': completed,
            'failed_chunks': failed,
        }
    
    def create_recovery_checkpoint(self, computation_id, completed_chunks, failed_chunks, partial_results):
        """
        Create a recovery checkpoint for resuming computation
        Enables recovery with better granularity
        """
        recovery_data = {
            'computation_id': computation_id,
            'checkpoint_time': time.time(),
            'completed_chunks': list(completed_chunks),
            'failed_chunks': list(failed_chunks),
            'partial_result_count': len(partial_results),
        }
        
        recovery_file = self.state_dir / f"recovery_{computation_id}.json"
        with open(recovery_file, 'w') as f:
            json.dump(recovery_data, f, indent=2)
        
        return recovery_file
    
    def mark_chunk_for_retry(self, chunk_id, start, end):
        """Mark a chunk for retry after opening circuit breaker"""
        if os.path.exists(self.active_file):
            with open(self.active_file, 'r') as f:
                state = json.load(f)
        else:
            state = {}
        
        if 'failed_chunks' not in state:
            state['failed_chunks'] = []
        
        # Check if chunk already marked as failed
        failed_ids = {c['chunk_id'] for c in state['failed_chunks']}
        
        if chunk_id not in failed_ids:
            state['failed_chunks'].append({
                'chunk_id': chunk_id,
                'rows': [start, end],
                'attempts': state.get('last_attempt_count', 0) + 1,
                'timestamp': time.time()
            })
            self._save_json_state(state)
    
    def can_resume_computation(self):
        """Check if computation can be resumed (not completed or failed)"""
        if not self.has_active_computation():
            return False
        
        status = self.get_computation_status()
        return status and status.get('status') not in ['completed', 'failed']

