package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"
)

type Sample struct {
	Index        int
	Operation    string
	Milliseconds float64
	Error        string
	value        any
}
type Metrics struct {
	Count    int     `json:"count"`
	Success  int     `json:"success"`
	Errors   int     `json:"errors"`
	MeanMS   float64 `json:"mean_ms"`
	MedianMS float64 `json:"median_ms"`
	P95MS    float64 `json:"p95_ms"`
}
type Summary struct {
	Transport     string             `json:"transport"`
	Concurrency   int                `json:"concurrency"`
	TotalSeconds  float64            `json:"total_seconds"`
	SuccessfulRPS float64            `json:"successful_rps"`
	Metrics       Metrics            `json:"metrics"`
	Operations    map[string]Metrics `json:"operations"`
	GroupSeconds  map[string]float64 `json:"group_seconds"`
}

func metrics(samples []Sample) Metrics {
	m := Metrics{Count: len(samples)}
	var values []float64
	for _, s := range samples {
		if s.Error != "" {
			m.Errors++
			continue
		}
		m.Success++
		values = append(values, s.Milliseconds)
		m.MeanMS += s.Milliseconds
	}
	if len(values) == 0 {
		return m
	}
	sort.Float64s(values)
	n := len(values)
	m.MeanMS /= float64(n)
	m.MedianMS = values[n/2]
	if n%2 == 0 {
		m.MedianMS = (values[n/2-1] + values[n/2]) / 2
	}
	m.P95MS = values[int(math.Ceil(.95*float64(n)))-1]
	return m
}
func batch(c *caller, group []Operation, concurrency int) ([]Sample, time.Duration) {
	samples := make([]Sample, len(group))
	jobs := make(chan int, len(group))
	for i := range group {
		jobs <- i
	}
	close(jobs)
	var wg sync.WaitGroup
	begin := time.Now()
	for w := 0; w < concurrency; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				start := time.Now()
				value, err := c.invoke(ctx, group[i])
				elapsed := time.Since(start)
				cancel()
				samples[i] = Sample{Index: i, Operation: group[i].Name(), Milliseconds: float64(elapsed) / float64(time.Millisecond), value: value}
				if err != nil {
					samples[i].Error = err.Error()
				}
			}
		}()
	}
	wg.Wait()
	return samples, time.Since(begin)
}
func validateBatch(samples []Sample, group []Operation, seed Seed) {
	validateWithIDs(samples, group, seed, map[int64]bool{})
}

func validateWithIDs(samples []Sample, group []Operation, seed Seed, ids map[int64]bool) {
	for i := range samples {
		if samples[i].Error != "" {
			continue
		}
		if err := validate(group[i], samples[i].value, seed); err != nil {
			samples[i].Error = err.Error()
			continue
		}
		if group[i].Action == "create" {
			var id int64
			switch v := normalized(samples[i].value).(type) {
			case User:
				id = v.ID
			case Leave:
				id = v.ID
			}
			if ids[id] {
				samples[i].Error = "duplicate generated ID"
			}
			ids[id] = true
		}
	}
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	if len(os.Args) < 2 {
		return fmt.Errorf("usage: bench seed | workload | run [flags]")
	}
	switch os.Args[1] {
	case "seed":
		return json.NewEncoder(os.Stdout).Encode(seedData())
	case "workload":
		return json.NewEncoder(os.Stdout).Encode(workload())
	case "run":
	default:
		return fmt.Errorf("unknown command")
	}
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	transport := flags.String("transport", "", "rest or grpc")
	addr := flags.String("addr", "127.0.0.1:8000", "Gateway address")
	concurrency := flags.Int("concurrency", 1, "1 or 10")
	output := flags.String("out", "", "output directory")
	if err := flags.Parse(os.Args[2:]); err != nil {
		return err
	}
	if *output == "" || (*concurrency != 1 && *concurrency != 10) {
		return fmt.Errorf("out is required; concurrency must be 1 or 10")
	}
	c, err := newCaller(*transport, *addr, *concurrency)
	if err != nil {
		return err
	}
	defer c.Close()
	seed := seedData()
	// Exactly 20 unmeasured, read-only requests; populate reusable client connections.
	for _, resource := range []string{"users", "leaves"} {
		group := []Operation{}
		for id := int64(1); id <= 10; id++ {
			group = append(group, Operation{Resource: resource, Action: "get", ID: id})
		}
		samples, _ := batch(c, group, *concurrency)
		validateBatch(samples, group, seed)
		for _, s := range samples {
			if s.Error != "" {
				return fmt.Errorf("warmup: %s", s.Error)
			}
		}
	}
	summary := Summary{Transport: *transport, Concurrency: *concurrency, Operations: map[string]Metrics{}, GroupSeconds: map[string]float64{}}
	all := []Sample{}
	for _, group := range workload() {
		var groupSamples []Sample
		var groupElapsed time.Duration
		ids := map[int64]bool{}
		// Bound retained response memory. Validation pauses between equal-sized
		// sub-batches are excluded from both versions' measured durations.
		for start := 0; start < len(group); start += responseBatchSize {
			end := start + responseBatchSize
			if end > len(group) {
				end = len(group)
			}
			samples, elapsed := batch(c, group[start:end], *concurrency)
			groupElapsed += elapsed
			validateWithIDs(samples, group[start:end], seed, ids)
			for i := range samples {
				samples[i].Index += start + len(all)
				samples[i].value = nil
			}
			groupSamples = append(groupSamples, samples...)
		}
		summary.TotalSeconds += groupElapsed.Seconds()
		summary.GroupSeconds[group[0].Name()] = groupElapsed.Seconds()
		summary.Operations[group[0].Name()] = metrics(groupSamples)
		all = append(all, groupSamples...)
		fmt.Fprintf(os.Stderr, "%s: %d requests, %.3f seconds, %d errors\n", group[0].Name(), len(groupSamples), groupElapsed.Seconds(), summary.Operations[group[0].Name()].Errors)
	}

	summary.Metrics = metrics(all)
	summary.SuccessfulRPS = float64(summary.Metrics.Success) / summary.TotalSeconds
	if err := os.MkdirAll(*output, 0755); err != nil {
		return err
	}
	f, err := os.Create(filepath.Join(*output, "samples.csv"))
	if err != nil {
		return err
	}
	writer := csv.NewWriter(f)
	err = writer.Write([]string{"index", "operation", "latency_ms", "success", "error"})
	for _, s := range all {
		if err == nil {
			err = writer.Write([]string{strconv.Itoa(s.Index), s.Operation, strconv.FormatFloat(s.Milliseconds, 'f', 6, 64), strconv.FormatBool(s.Error == ""), s.Error})
		}
	}
	writer.Flush()
	if err == nil {
		err = writer.Error()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	data, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(*output, "summary.json"), append(data, '\n'), 0644); err != nil {
		return err
	}
	if summary.Metrics.Errors > 0 {
		return fmt.Errorf("%d measured requests failed; results saved", summary.Metrics.Errors)
	}
	return nil
}
