import { Step, StepLabel, Stepper, Typography } from '@mui/material';
import { buildTimeline } from '../lib/timeline.ts';
import { statusTexts, type ApplicationStatus } from '../lib/texts.ts';

/**
 * design-tokens.md: пройденные шаги — бордо, текущий — тиффани, будущие — контур,
 * остановленный (REJECTED, CANCELLED) — серый #97939B.
 */
export function ApplicationTimeline({ status }: { status: ApplicationStatus }) {
  const steps = buildTimeline(status, statusTexts(status).timeline);
  const active = steps.findIndex((s) => s.state !== 'done');
  return (
    <Stepper
      activeStep={active === -1 ? steps.length : active}
      alternativeLabel
      aria-label="Этапы рассмотрения заявки"
      sx={{
        overflowX: 'auto',
        py: 1,
        '& .MuiStepIcon-root.Mui-completed': { color: 'primary.main' },
        '& .MuiStepIcon-root.Mui-active': { color: 'secondary.main' },
        '& .step-stopped .MuiStepIcon-root, & .step-stopped .MuiStepIcon-root.Mui-active': { color: 'grey.400' },
        '& .step-stopped .MuiStepLabel-label': { color: 'text.secondary', fontWeight: 500 },
      }}
    >
      {steps.map((step) => (
        <Step
          key={step.label}
          completed={step.state === 'done'}
          className={step.state === 'stopped' ? 'step-stopped' : undefined}
        >
          <StepLabel
            optional={step.state === 'attention' ? (
              <Typography variant="caption" color="info.main">Нужно действие</Typography>
            ) : undefined}
            aria-current={step.state === 'current' || step.state === 'attention' ? 'step' : undefined}
          >
            {step.label}
          </StepLabel>
        </Step>
      ))}
    </Stepper>
  );
}
