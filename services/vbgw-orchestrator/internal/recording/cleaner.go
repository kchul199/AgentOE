/**
 * @file cleaner.go
 * @description 녹음 파일 정리 — hourly ticker, age (30d) + quota (MB) 기반
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | hourly 정리 + oldest-first 삭제
 * ─────────────────────────────────────────
 */

package recording

import (
	"context"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"time"

	"vbgw-orchestrator/internal/metrics"
)

// Cleaner periodically removes old and excess recording files.
type Cleaner struct {
	dir     string
	maxDays int
	maxMB   int64
	enabled bool
}

// NewCleaner creates a recording cleaner.
func NewCleaner(dir string, maxDays int, maxMB int64, enabled bool) *Cleaner {
	return &Cleaner{dir: dir, maxDays: maxDays, maxMB: maxMB, enabled: enabled}
}

// Run starts the hourly cleanup ticker. Blocks until context is done.
func (c *Cleaner) Run(ctx context.Context) {
	if !c.enabled {
		slog.Info("Recording cleaner disabled")
		return
	}

	slog.Info("Recording cleaner started", "dir", c.dir, "max_days", c.maxDays, "max_mb", c.maxMB)
	ticker := time.NewTicker(1 * time.Hour)
	defer ticker.Stop()

	// Initial cleanup on start
	c.cleanup()

	for {
		select {
		case <-ctx.Done():
			slog.Info("Recording cleaner stopped")
			return
		case <-ticker.C:
			c.cleanup()
		}
	}
}

// CleanupNow triggers an immediate cleanup (for testing).
func (c *Cleaner) CleanupNow() {
	if !c.enabled {
		return
	}
	c.cleanup()
}

type fileEntry struct {
	path    string
	modTime time.Time
	size    int64
}

func (c *Cleaner) cleanup() {
	cutoff := time.Now().Add(-time.Duration(c.maxDays) * 24 * time.Hour)
	var files []fileEntry
	var totalSize int64
	var removedCount int
	var removedBytes int64

	err := filepath.Walk(c.dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		if filepath.Ext(path) != ".wav" {
			return nil
		}
		files = append(files, fileEntry{path: path, modTime: info.ModTime(), size: info.Size()})
		totalSize += info.Size()
		return nil
	})
	if err != nil {
		slog.Error("Recording cleanup walk error", "err", err)
		return
	}

	// Remove files older than maxDays
	var remaining []fileEntry
	for _, f := range files {
		if f.modTime.Before(cutoff) {
			if err := os.Remove(f.path); err != nil {
				slog.Error("Failed to remove old recording", "path", f.path, "err", err)
				continue
			}
			slog.Info("Removed old recording", "path", f.path, "age_days", time.Since(f.modTime).Hours()/24)
			removedCount++
			removedBytes += f.size
			totalSize -= f.size
		} else {
			remaining = append(remaining, f)
		}
	}

	// Remove oldest files if over quota
	maxBytes := c.maxMB * 1024 * 1024
	if totalSize > maxBytes {
		sort.Slice(remaining, func(i, j int) bool {
			return remaining[i].modTime.Before(remaining[j].modTime)
		})

		for _, f := range remaining {
			if totalSize <= maxBytes {
				break
			}
			if err := os.Remove(f.path); err != nil {
				slog.Error("Failed to remove quota recording", "path", f.path, "err", err)
				continue
			}
			slog.Info("Removed recording (quota)", "path", f.path)
			removedCount++
			removedBytes += f.size
			totalSize -= f.size
		}
	}

	if removedCount > 0 {
		metrics.RecordingCleanupFiles.Add(float64(removedCount))
		metrics.RecordingCleanupBytes.Add(float64(removedBytes))
		slog.Info("Recording cleanup complete", "removed", removedCount, "freed_mb", removedBytes/1024/1024)
	}
}
