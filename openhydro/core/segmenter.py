"""
OpenHydro Dynamic Segmentation Engine
Implements dynamic chunk allocation and dynamic chunk splitting (The IDM Secret Sauce).
"""

from typing import List, Optional
from .task import Chunk, DownloadTask

# Minimum remaining bytes in a chunk required to split it into two (e.g., 1MB)
MIN_SPLIT_THRESHOLD = 1 * 1024 * 1024


class DynamicSegmenter:
    @staticmethod
    def create_initial_chunks(total_bytes: int, num_connections: int) -> List[Chunk]:
        """
        Creates the initial range chunks for the download.
        Guarantees that chunk_size >= 1 and no overlapping or invalid ranges.
        """
        if total_bytes <= 0 or num_connections <= 1:
            return [Chunk(id=0, start_byte=0, end_byte=max(0, total_bytes - 1), current_byte=0)]

        # Bound connections: cannot exceed total_bytes and bounded between 1 and 32
        effective_connections = max(1, min(num_connections, total_bytes, 32))
        chunk_size = total_bytes // effective_connections

        chunks: List[Chunk] = []
        for i in range(effective_connections):
            start = i * chunk_size
            end = start + chunk_size - 1
            if i == effective_connections - 1:
                end = total_bytes - 1  # Last chunk takes the rest
            chunks.append(Chunk(id=i, start_byte=start, end_byte=end, current_byte=start))

        return chunks

    @staticmethod
    def split_largest_chunk_unlocked(task: DownloadTask) -> Optional[Chunk]:
        """
        IDM Dynamic Subdivision Algorithm (unlocked core):
        Scans all active downloading chunks to find the one with the largest remaining bytes.
        Splits it in half if remaining >= MIN_SPLIT_THRESHOLD, and returns the new chunk.
        Caller must hold task/segmenter lock.
        """
        candidate_chunk: Optional[Chunk] = None
        max_remaining: int = 0

        for chunk in task.chunks:
            if chunk.status == "downloading":
                rem = chunk.end_byte - chunk.current_byte + 1
                if rem > max_remaining:
                    max_remaining = rem
                    candidate_chunk = chunk

        if not candidate_chunk or max_remaining < MIN_SPLIT_THRESHOLD:
            return None

        # Split remaining range in half
        current = chunk.current_byte
        original_end = candidate_chunk.end_byte
        remaining = original_end - current + 1
        half = remaining // 2

        midpoint = current + half - 1
        if midpoint <= current or midpoint >= original_end:
            return None

        # Adjust original chunk's end
        candidate_chunk.end_byte = midpoint

        # Create new chunk for the idle worker
        new_chunk_id = len(task.chunks)
        new_chunk = Chunk(
            id=new_chunk_id,
            start_byte=midpoint + 1,
            end_byte=original_end,
            current_byte=midpoint + 1,
            status="idle"
        )
        task.chunks.append(new_chunk)
        return new_chunk
