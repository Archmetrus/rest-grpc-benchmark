package main

import (
	"testing"
)

func TestWorkloadAndSeed(t *testing.T) {
	s := seedData()
	if len(s.Users) != int(seedUsers) || len(s.Leaves) != int(2*seedUsers) {
		t.Fatal("incorrect seed size")
	}
	owners := map[int64]int{}
	for i, l := range s.Leaves {
		if l.ID != int64(i+1) {
			t.Fatal("nonsequential leave IDs")
		}
		owners[l.UserID]++
	}
	for _, u := range s.Users {
		if owners[u.ID] != 2 {
			t.Fatal("each user must have two leaves")
		}
	}
	groups := workload()
	if len(groups) != 10 {
		t.Fatal("want ten groups")
	}
	for _, g := range groups {
		if len(g) != int(callsPerOperation) {
			t.Fatal("want configured calls per group")
		}
		seen := map[int64]bool{}
		for _, o := range g {
			if seen[o.ID] {
				t.Fatal("operations must not race on a target")
			}
			seen[o.ID] = true
			if o.Action == "get" && (o.ID < 1 || o.ID > callsPerOperation) {
				t.Fatal("get range")
			}
			if o.Action == "update" && (o.ID < callsPerOperation+1 || o.ID > 2*callsPerOperation) {
				t.Fatal("update range")
			}
			if o.Action == "delete" && (o.ID < 2*callsPerOperation+1 || o.ID > 3*callsPerOperation) {
				t.Fatal("delete range")
			}
		}
	}
}
func TestValidationDetectsWrongContents(t *testing.T) {
	s := seedData()
	o := Operation{Resource: "users", Action: "get", ID: 1}
	if err := validate(o, s.Users[0], s); err != nil {
		t.Fatal(err)
	}
	bad := s.Users[0]
	bad.Email = "wrong@example.com"
	if err := validate(o, bad, s); err == nil {
		t.Fatal("corrupt response accepted")
	}
	group := workload()[2]
	a := group[0].User
	a.ID = seedUsers + 1
	b := group[1].User
	b.ID = seedUsers + 1
	samples := []Sample{{value: a}, {value: b}}
	validateBatch(samples, group[:2], s)
	if samples[1].Error == "" {
		t.Fatal("duplicate created IDs accepted")
	}
}
func TestMetricsAndFailureAccounting(t *testing.T) {
	s := []Sample{}
	for i := 1; i <= 100; i++ {
		s = append(s, Sample{Milliseconds: float64(i)})
	}
	s = append(s, Sample{Milliseconds: 9999, Error: "timeout"})
	m := metrics(s)
	if m.Count != 101 || m.Success != 100 || m.Errors != 1 || m.MedianMS != 50.5 || m.P95MS != 95 || m.MeanMS != 50.5 {
		t.Fatalf("bad metrics: %+v", m)
	}
	if m := metrics([]Sample{{Error: "failed"}}); m.Success != 0 || m.Errors != 1 || m.P95MS != 0 {
		t.Fatal(m)
	}
}

func TestDuplicateIDsAcrossResponseBatches(t *testing.T) {
	s := seedData()
	group := workload()[2]
	seen := map[int64]bool{}
	a := group[0].User
	a.ID = seedUsers + 1
	b := group[1].User
	b.ID = seedUsers + 1
	first := []Sample{{value: a}}
	second := []Sample{{value: b}}
	validateWithIDs(first, group[:1], s, seen)
	validateWithIDs(second, group[1:2], s, seen)
	if first[0].Error != "" || second[0].Error == "" {
		t.Fatal("cross-batch duplicate not detected")
	}
}
