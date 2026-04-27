/**
 * @file otel.go
 * @description OpenTelemetry 트레이싱 초기화 및 SDK 설정
 */

package telemetry

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"vbgw-orchestrator/internal/config"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.17.0"
)

// InitOTel initializes an OTLP exporter, and configures the corresponding trace provider.
func InitOTel(ctx context.Context, cfg *config.Config, nodeID string) (func(context.Context) error, error) {
	if !cfg.OTelEnabled {
		return func(context.Context) error { return nil }, nil
	}

	res, err := resource.New(ctx,
		resource.WithAttributes(
			semconv.ServiceName("vbgw-orchestrator"),
			semconv.ServiceInstanceID(nodeID),
			semconv.DeploymentEnvironment(cfg.RuntimeProfile),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create resource: %w", err)
	}

	// Set up a trace exporter
	traceExporter, err := otlptracegrpc.New(ctx,
		otlptracegrpc.WithInsecure(),
		otlptracegrpc.WithEndpoint(cfg.OTelEndpoint),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create trace exporter: %w", err)
	}

	// Register the TraceProvider with the appropriate sampler
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(traceExporter),
		sdktrace.WithResource(res),
		sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.AlwaysSample())),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))

	slog.Info("OpenTelemetry initialized", "endpoint", cfg.OTelEndpoint)

	return tp.Shutdown, nil
}
