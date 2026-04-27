package recording

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func setupTestDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	return dir
}

func createFile(t *testing.T, dir, name string, size int, age time.Duration) {
	t.Helper()
	path := filepath.Join(dir, name)
	data := make([]byte, size)
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatal(err)
	}
	modTime := time.Now().Add(-age)
	os.Chtimes(path, modTime, modTime)
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func TestCleaner_RemovesOldFiles(t *testing.T) {
	dir := setupTestDir(t)

	// Create files: 2 old (31 days), 1 recent (1 day)
	createFile(t, dir, "old1.wav", 1024, 31*24*time.Hour)
	createFile(t, dir, "old2.wav", 1024, 35*24*time.Hour)
	createFile(t, dir, "recent.wav", 1024, 1*24*time.Hour)

	c := NewCleaner(dir, 30, 1024, true)
	c.CleanupNow()

	if fileExists(filepath.Join(dir, "old1.wav")) {
		t.Fatal("expected old1.wav to be deleted (31 days old)")
	}
	if fileExists(filepath.Join(dir, "old2.wav")) {
		t.Fatal("expected old2.wav to be deleted (35 days old)")
	}
	if !fileExists(filepath.Join(dir, "recent.wav")) {
		t.Fatal("expected recent.wav to be kept (1 day old)")
	}
}

func TestCleaner_QuotaDeletesOldestFirst(t *testing.T) {
	dir := setupTestDir(t)

	// Create 3 files, total 3MB. Quota = 2MB.
	createFile(t, dir, "oldest.wav", 1024*1024, 10*24*time.Hour)
	createFile(t, dir, "middle.wav", 1024*1024, 5*24*time.Hour)
	createFile(t, dir, "newest.wav", 1024*1024, 1*24*time.Hour)

	// maxDays=365 (won't trigger age cleanup), maxMB=2
	c := NewCleaner(dir, 365, 2, true)
	c.CleanupNow()

	// Oldest should be deleted first to get under 2MB
	if fileExists(filepath.Join(dir, "oldest.wav")) {
		t.Fatal("expected oldest.wav to be deleted by quota")
	}
	if !fileExists(filepath.Join(dir, "middle.wav")) {
		t.Fatal("expected middle.wav to be kept")
	}
	if !fileExists(filepath.Join(dir, "newest.wav")) {
		t.Fatal("expected newest.wav to be kept")
	}
}

func TestCleaner_SkipsNonWavFiles(t *testing.T) {
	dir := setupTestDir(t)

	createFile(t, dir, "old.wav", 1024, 31*24*time.Hour)
	createFile(t, dir, "old.txt", 1024, 31*24*time.Hour)
	createFile(t, dir, "old.log", 1024, 31*24*time.Hour)

	c := NewCleaner(dir, 30, 1024, true)
	c.CleanupNow()

	if fileExists(filepath.Join(dir, "old.wav")) {
		t.Fatal("expected old.wav to be deleted")
	}
	if !fileExists(filepath.Join(dir, "old.txt")) {
		t.Fatal("expected old.txt to be kept (not .wav)")
	}
	if !fileExists(filepath.Join(dir, "old.log")) {
		t.Fatal("expected old.log to be kept (not .wav)")
	}
}

func TestCleaner_DisabledDoesNothing(t *testing.T) {
	dir := setupTestDir(t)
	createFile(t, dir, "old.wav", 1024, 31*24*time.Hour)

	c := NewCleaner(dir, 30, 1024, false) // enabled=false
	c.CleanupNow()

	if !fileExists(filepath.Join(dir, "old.wav")) {
		t.Fatal("expected old.wav to be kept when cleaner is disabled")
	}
}

func TestCleaner_EmptyDirectory(t *testing.T) {
	dir := setupTestDir(t)
	c := NewCleaner(dir, 30, 1024, true)
	c.CleanupNow() // should not panic
}
