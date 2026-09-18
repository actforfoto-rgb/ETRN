package ru.komus.etrnprobe;

import org.junit.Test;
import java.io.IOException;
import java.util.*;
import static org.junit.Assert.*;
import static ru.komus.etrnprobe.PhoneBatchEngine.*;

public class PhoneBatchEngineTest {
    static class Memory implements Store {
        Snapshot data; boolean fail;
        public Snapshot load(){return data==null?null:data.copy();}
        public void save(Snapshot s)throws Exception{if(fail)throw new IOException("disk");data=s.copy();}
    }

    static List<Document> docs(int n){
        List<Document> d=new ArrayList<>();
        for(int i=0;i<n;i++) d.add(new Document("d"+i,"r"+i,"s"+i,i%2==0?"Принят":"Выдан","acct"));
        return d;
    }

    static class Fake implements Gateway {
        String current="acct";
        int prepares,submits,polls,executes,reads;
        int failSubmitAt=-1,failExecuteAt=-1;
        boolean changed,readback=true;
        final List<List<PreparedFile>> submitted=new ArrayList<>();
        final Map<String,List<PreparedFile>> operations=new LinkedHashMap<>();
        final Set<String> done=new HashSet<>();
        final Map<String,PackageStatus> statuses=new HashMap<>();
        final Map<String,List<Signature>> supplied=new HashMap<>();

        public String principal(){return current;}
        public List<PreparedFile> prepare(Document d){
            prepares++;
            return Collections.singletonList(new PreparedFile(
                d.id,"a"+d.id,"etrn-"+d.id+".xml","https://example.invalid/"+d.id,"h"+d.id));
        }
        public String submitPackage(GoskeyIpPackagePlan.Recipient r,List<PreparedFile> files)throws Exception{
            submits++;
            submitted.add(new ArrayList<>(files));
            if(submits==failSubmitAt)throw new IOException("submit timeout");
            String op="op"+submits;
            operations.put(op,new ArrayList<>(files));
            statuses.put(op,PackageStatus.SIGNED);
            return op;
        }
        public PackagePoll pollPackage(String operationId){
            polls++;
            PackageStatus st=statuses.getOrDefault(operationId,PackageStatus.UNKNOWN);
            List<Signature> sig=supplied.get(operationId);
            if(sig==null){
                sig=new ArrayList<>();
                for(PreparedFile f:operations.getOrDefault(operationId,Collections.emptyList()))
                    sig.add(new Signature(f.document,f.attachment,f.hash,"acct",new byte[]{1,2,3}));
            }
            return new PackagePoll(st,sig);
        }
        public boolean documentUnchanged(Document d,List<PreparedFile> files){return !changed;}
        public void execute(Document d,List<Signature> signatures)throws Exception{
            executes++;
            for(Signature s:signatures)assertEquals(d.id,s.document);
            done.add(d.id);
            if(executes==failExecuteAt)throw new IOException("execute timeout");
        }
        public boolean confirmed(Document d,List<PreparedFile> files){reads++;return readback&&done.contains(d.id);}
    }

    static GoskeyIpPackagePlan.Recipient recipient(){
        return new GoskeyIpPackagePlan.Recipient("123456789012345","","123456789");
    }
    static PhoneBatchEngine engine(int n,Fake f,Memory m)throws Exception{
        return new PhoneBatchEngine(docs(n),recipient(),f,(file,s,p)->true,m);
    }
    static PhoneBatchEngine ready(int n,Fake f,Memory m)throws Exception{
        PhoneBatchEngine e=engine(n,f,m);e.prepare(true);return e;
    }
    static PhoneBatchEngine waiting(int n,Fake f,Memory m)throws Exception{
        PhoneBatchEngine e=ready(n,f,m);e.submit(true);return e;
    }

    @Test public void fiftyDocumentsUseThreeGoskeyJobsThenFiftyExecuteActions()throws Exception{
        Fake f=new Fake();Memory m=new Memory();PhoneBatchEngine e=waiting(50,f,m);
        assertEquals(3,f.submits);
        assertEquals(20,f.submitted.get(0).size());
        assertEquals(20,f.submitted.get(1).size());
        assertEquals(10,f.submitted.get(2).size());
        e.pollAndApply();
        assertEquals(Phase.COMPLETE,e.snapshot().phase);
        assertEquals(3,f.polls);
        assertEquals(50,f.executes);
        assertEquals(50,e.snapshot().confirmed.size());
    }

    @Test public void mixedStageActionsAreAllowedBecauseExecuteIsPerDocument()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=waiting(4,f,new Memory());
        e.pollAndApply();
        assertEquals(Phase.COMPLETE,e.snapshot().phase);
    }

    @Test public void operationIdsAreNotCompletion()throws Exception{
        PhoneBatchEngine e=waiting(21,new Fake(),new Memory());
        assertEquals(Phase.WAITING,e.snapshot().phase);
        assertEquals(2,e.snapshot().packages.size());
        assertTrue(e.snapshot().confirmed.isEmpty());
    }

    @Test public void preparationAndSubmissionNeedSeparateConsent()throws Exception{
        Fake f=new Fake();Memory m=new Memory();PhoneBatchEngine e=engine(2,f,m);
        assertThrows(IllegalStateException.class,()->e.prepare(false));
        assertEquals(0,f.prepares);
        e.prepare(true);
        assertThrows(IllegalStateException.class,()->e.submit(false));
        assertEquals(0,f.submits);
    }

    @Test public void packageSubmitTimeoutIsNeverBlindlyRetried()throws Exception{
        Fake f=new Fake();f.failSubmitAt=2;Memory m=new Memory();PhoneBatchEngine e=ready(25,f,m);
        assertThrows(IOException.class,()->e.submit(true));
        assertEquals(2,f.submits);
        assertEquals(Phase.UNKNOWN,e.snapshot().phase);
        assertEquals(PackageStatus.WAITING,e.snapshot().packages.get(0).status);
        assertEquals(PackageStatus.UNKNOWN,e.snapshot().packages.get(1).status);
        PhoneBatchEngine restarted=engine(25,f,m);
        assertThrows(IllegalStateException.class,()->restarted.submit(true));
        assertEquals(2,f.submits);
    }

    @Test public void oneWaitingPackagePreventsPrematureExecute()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=waiting(25,f,new Memory());
        f.statuses.put("op2",PackageStatus.WAITING);
        e.pollAndApply();
        assertEquals(0,f.executes);
        assertEquals(Phase.WAITING,e.snapshot().phase);
        assertEquals(PackageStatus.SIGNED,e.snapshot().packages.get(0).status);
        assertEquals(PackageStatus.WAITING,e.snapshot().packages.get(1).status);
    }

    @Test public void refusalInOnePackageBlocksAllSabyExecute()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=waiting(25,f,new Memory());
        f.statuses.put("op2",PackageStatus.REFUSED);
        e.pollAndApply();
        assertEquals(Phase.BLOCKED,e.snapshot().phase);
        assertEquals(0,f.executes);
    }

    @Test public void missingSignatureInOnePackageBlocksAllSabyExecute()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=waiting(2,f,new Memory());
        List<PreparedFile> p=f.operations.get("op1");
        f.supplied.put("op1",Collections.singletonList(
            new Signature(p.get(0).document,p.get(0).attachment,p.get(0).hash,"acct",new byte[]{1})));
        e.pollAndApply();
        assertEquals(Phase.BLOCKED,e.snapshot().phase);
        assertEquals(0,f.executes);
    }

    @Test public void foreignSignatureBlocksAllSabyExecute()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=waiting(2,f,new Memory());
        List<PreparedFile> p=f.operations.get("op1");
        f.supplied.put("op1",Arrays.asList(
            new Signature(p.get(0).document,p.get(0).attachment,p.get(0).hash,"acct",new byte[]{1}),
            new Signature("other","x","h","acct",new byte[]{1})
        ));
        e.pollAndApply();
        assertEquals(Phase.BLOCKED,e.snapshot().phase);
        assertEquals(0,f.executes);
    }

    @Test public void changedHashBlocksAllSabyExecute()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=waiting(2,f,new Memory());
        List<PreparedFile> p=f.operations.get("op1");
        f.supplied.put("op1",Arrays.asList(
            new Signature(p.get(0).document,p.get(0).attachment,"changed","acct",new byte[]{1}),
            new Signature(p.get(1).document,p.get(1).attachment,p.get(1).hash,"acct",new byte[]{1})
        ));
        e.pollAndApply();
        assertEquals(Phase.BLOCKED,e.snapshot().phase);
        assertEquals(0,f.executes);
    }

    @Test public void verifierFailureBlocksBeforeAnyExecute()throws Exception{
        Fake f=new Fake();Memory m=new Memory();
        PhoneBatchEngine e=new PhoneBatchEngine(docs(2),recipient(),f,(file,s,p)->false,m);
        e.prepare(true);e.submit(true);e.pollAndApply();
        assertEquals(Phase.BLOCKED,e.snapshot().phase);
        assertEquals(0,f.executes);
    }

    @Test public void accountSwitchBlocksFurtherCalls()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=ready(2,f,new Memory());f.current="other";
        assertThrows(IllegalStateException.class,()->e.submit(true));
        assertEquals(0,f.submits);
    }

    @Test public void changedDocumentBeforeSubmittingBlocksWithoutGoskeySideEffect()throws Exception{
        Fake f=new Fake();PhoneBatchEngine e=ready(2,f,new Memory());f.changed=true;
        e.submit(true);
        assertEquals(Phase.BLOCKED,e.snapshot().phase);
        assertEquals(0,f.submits);
    }

    @Test public void executeTimeoutCanOnlyRecoverByReadbackNotReapply()throws Exception{
        Fake f=new Fake();f.failExecuteAt=1;Memory m=new Memory();PhoneBatchEngine e=waiting(2,f,m);
        assertThrows(IOException.class,()->e.pollAndApply());
        assertEquals(1,f.executes);
        PhoneBatchEngine restarted=engine(2,f,m);
        f.failExecuteAt=-1;
        restarted.reconcileExecute();
        assertEquals(1,restarted.snapshot().confirmed.size());
        // Resume uses already collected signatures and does not resubmit Goskey jobs.
        restarted.pollAndApply();
        assertEquals(Phase.COMPLETE,restarted.snapshot().phase);
        assertEquals(2,f.executes);
        assertEquals(1,f.submits);
    }

    @Test public void failedReadbackNeverReexecutesBlindly()throws Exception{
        Fake f=new Fake();f.readback=false;Memory m=new Memory();PhoneBatchEngine e=waiting(2,f,m);
        e.pollAndApply();
        assertEquals(Phase.UNKNOWN,e.snapshot().phase);
        assertEquals(1,f.executes);
        PhoneBatchEngine restarted=engine(2,f,m);
        restarted.reconcileExecute();
        assertEquals(Phase.UNKNOWN,restarted.snapshot().phase);
        assertEquals(1,f.executes);
    }

    @Test public void duplicateDocumentPlanIsRejected()throws Exception{
        List<Document> d=docs(2);d.set(1,d.get(0));
        assertThrows(IllegalArgumentException.class,
            ()->new PhoneBatchEngine(d,recipient(),new Fake(),(f,s,p)->true,new Memory()));
    }

    @Test public void changedRevisionCannotResumeOldJournal()throws Exception{
        Fake f=new Fake();Memory m=new Memory();ready(2,f,m);
        List<Document> d=docs(2);d.set(0,new Document("d0","new","s0","Принят","acct"));
        assertThrows(IllegalStateException.class,
            ()->new PhoneBatchEngine(d,recipient(),f,(x,s,p)->true,m));
    }

    @Test public void storageFailurePreventsPackageNetworkSideEffect()throws Exception{
        Fake f=new Fake();Memory m=new Memory();PhoneBatchEngine e=ready(2,f,m);m.fail=true;
        assertThrows(IOException.class,()->e.submit(true));
        assertEquals(0,f.submits);
    }
}
